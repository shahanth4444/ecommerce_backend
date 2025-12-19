import json
import redis
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import update, delete
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.encoders import jsonable_encoder

# Local imports
from .database import engine, Base, get_db
from . import models, schemas, auth
from .worker import send_order_email

app = FastAPI(title="E-Commerce API")

# Redis
cache = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        # Caution: In real prod, use Alembic migrations. 
        # Here we rely on create_all to add new tables.
        await conn.run_sync(Base.metadata.create_all)

# --- AUTH ---
@app.post("/register", response_model=schemas.Token)
async def register(user: schemas.UserCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.User).where(models.User.email == user.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed_pwd = auth.get_password_hash(user.password)
    new_user = models.User(email=user.email, hashed_password=hashed_pwd, role=user.role.value)
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    access_token = auth.create_access_token(data={"sub": new_user.email, "role": new_user.role})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/login", response_model=schemas.Token)
async def login(user_credentials: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.User).where(models.User.email == user_credentials.username))
    user = result.scalar_one_or_none()
    if not user or not auth.verify_password(user_credentials.password, user.hashed_password):
        raise HTTPException(status_code=403, detail="Invalid Credentials")
    access_token = auth.create_access_token(data={"sub": user.email, "role": user.role})
    return {"access_token": access_token, "token_type": "bearer"}

# --- PRODUCTS (Updated with Filter & Sort) ---
@app.post("/products", response_model=schemas.ProductOut)
async def create_product(product: schemas.ProductCreate, db: AsyncSession = Depends(get_db), current_user: schemas.TokenData = Depends(auth.get_current_user)):
    if current_user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Only Admins can add products")
    new_product = models.Product(**product.dict())
    db.add(new_product)
    await db.commit()
    await db.refresh(new_product)
    try: cache.delete("products_list")
    except: pass
    return new_product

@app.get("/products", response_model=List[schemas.ProductOut])
async def get_products(
    category: Optional[str] = None,
    sort_by_price: Optional[str] = Query(None, regex="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db)
):
    # Note: Caching logic complicated by filters, skipping cache if filters present for simplicity
    if not category and not sort_by_price:
        try:
            cached = cache.get("products_list")
            if cached: return json.loads(cached)
        except: pass

    query = select(models.Product)
    
    # 🔥 FILTERING
    if category:
        query = query.where(models.Product.category == category)
    
    # 🔥 SORTING
    if sort_by_price == "asc":
        query = query.order_by(models.Product.price.asc())
    elif sort_by_price == "desc":
        query = query.order_by(models.Product.price.desc())

    result = await db.execute(query)
    products = result.scalars().all()

    # Only cache if no filters applied
    if not category and not sort_by_price:
        try:
            cache.set("products_list", json.dumps(jsonable_encoder(products)), ex=600)
        except: pass
        
    return products

# --- CART ROUTES (NEW) ---
@app.post("/cart/items", response_model=schemas.CartOut)
async def add_to_cart(
    item: schemas.CartItemAdd, 
    db: AsyncSession = Depends(get_db),
    current_user: schemas.TokenData = Depends(auth.get_current_user)
):
    # Get User
    result = await db.execute(select(models.User).where(models.User.email == current_user.email))
    user = result.scalar_one()

    # Get or Create Cart
    res = await db.execute(select(models.Cart).where(models.Cart.user_id == user.id))
    cart = res.scalar_one_or_none()
    if not cart:
        cart = models.Cart(user_id=user.id)
        db.add(cart)
        await db.commit()
        await db.refresh(cart)

    # Check if item exists in cart
    res = await db.execute(select(models.CartItem).where(models.CartItem.cart_id == cart.id, models.CartItem.product_id == item.product_id))
    existing_item = res.scalar_one_or_none()

    if existing_item:
        existing_item.quantity += item.quantity
    else:
        new_item = models.CartItem(cart_id=cart.id, product_id=item.product_id, quantity=item.quantity)
        db.add(new_item)
    
    await db.commit()
    
    # Return full cart
    stmt = select(models.Cart).where(models.Cart.id == cart.id).options(selectinload(models.Cart.items).selectinload(models.CartItem.product))
    res = await db.execute(stmt)
    return res.scalar_one()

@app.get("/cart", response_model=schemas.CartOut)
async def view_cart(db: AsyncSession = Depends(get_db), current_user: schemas.TokenData = Depends(auth.get_current_user)):
    result = await db.execute(select(models.User).where(models.User.email == current_user.email))
    user = result.scalar_one()

    stmt = select(models.Cart).where(models.Cart.user_id == user.id).options(selectinload(models.Cart.items).selectinload(models.CartItem.product))
    res = await db.execute(stmt)
    cart = res.scalar_one_or_none()
    
    if not cart:
        # Return empty temp cart structure if none exists
        return {"id": 0, "items": []}
        
    return cart

@app.delete("/cart/items/{product_id}")
async def remove_from_cart(product_id: int, db: AsyncSession = Depends(get_db), current_user: schemas.TokenData = Depends(auth.get_current_user)):
    result = await db.execute(select(models.User).where(models.User.email == current_user.email))
    user = result.scalar_one()

    # Find Cart
    res = await db.execute(select(models.Cart).where(models.Cart.user_id == user.id))
    cart = res.scalar_one_or_none()
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")

    # Delete Item
    await db.execute(delete(models.CartItem).where(models.CartItem.cart_id == cart.id, models.CartItem.product_id == product_id))
    await db.commit()
    return {"message": "Item removed"}

# --- ORDER ROUTES (UPDATED: FROM CART) ---
@app.post("/orders", response_model=schemas.OrderOut)
async def create_order_from_cart(
    db: AsyncSession = Depends(get_db),
    current_user: schemas.TokenData = Depends(auth.get_current_user)
):
    # 1. Get User & Cart
    user_res = await db.execute(select(models.User).where(models.User.email == current_user.email))
    user = user_res.scalar_one()

    cart_res = await db.execute(select(models.Cart).where(models.Cart.user_id == user.id).options(selectinload(models.Cart.items)))
    cart = cart_res.scalar_one_or_none()

    if not cart or not cart.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    total_price = 0.0
    
    # 2. Transaction Start
    try:
        new_order = models.Order(user_id=user.id, total_price=0, status="PROCESSING")
        db.add(new_order)
        await db.flush()

        # 3. Process Cart Items
        for cart_item in cart.items:
            # Fetch Product
            res = await db.execute(select(models.Product).where(models.Product.id == cart_item.product_id))
            product = res.scalar_one_or_none()

            if not product:
                raise HTTPException(status_code=404, detail=f"Product {cart_item.product_id} not found")
            
            if product.stock_quantity < cart_item.quantity:
                raise HTTPException(status_code=400, detail=f"Out of stock: {product.name}")

            # Optimistic Locking
            stmt = (
                update(models.Product)
                .where(models.Product.id == product.id)
                .where(models.Product.version == product.version)
                .values(stock_quantity=models.Product.stock_quantity - cart_item.quantity, version=models.Product.version + 1)
                .execution_options(synchronize_session="fetch")
            )
            update_result = await db.execute(stmt)

            if update_result.rowcount == 0:
                raise HTTPException(status_code=409, detail=f"Stock changed for {product.name}. Please retry.")

            order_item = models.OrderItem(
                order_id=new_order.id, 
                product_id=product.id, 
                quantity=cart_item.quantity, 
                price_at_purchase=product.price
            )
            db.add(order_item)
            total_price += product.price * cart_item.quantity

        # 4. Finalize
        new_order.total_price = total_price
        new_order.status = "CONFIRMED"
        
        # 🔥 CLEAR CART
        await db.execute(delete(models.CartItem).where(models.CartItem.cart_id == cart.id))

        await db.commit()
        await db.refresh(new_order)
        
        # Async Task
        try:
            cache.delete("products_list")
            send_order_email.delay(user.email, new_order.id)
        except: pass
            
        return new_order

    except Exception as e:
        await db.rollback()
        raise e
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from .models import Role

# --- USER ---
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: Role = Role.CUSTOMER

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None
    role: Optional[str] = None

# --- PRODUCT ---
class ProductBase(BaseModel):
    name: str
    category: str = "Electronics" # 🔥 NEW
    price: float
    stock_quantity: int

class ProductCreate(ProductBase):
    pass

class ProductOut(ProductBase):
    id: int
    version: int
    class Config:
        from_attributes = True

# --- CART (NEW) ---
class CartItemAdd(BaseModel):
    product_id: int
    quantity: int

class CartItemOut(BaseModel):
    id: int
    product_id: int
    quantity: int
    product: ProductOut # Nested Product details
    class Config:
        from_attributes = True

class CartOut(BaseModel):
    id: int
    items: List[CartItemOut]
    class Config:
        from_attributes = True

# --- ORDER ---
class OrderOut(BaseModel):
    id: int
    total_price: float
    status: str
    class Config:
        from_attributes = True
import csv
import io
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request as FastAPIRequest
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Boolean, Float
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from pydantic import BaseModel, Field

DATABASE_URL = "sqlite:///./asset_management.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Role(str, Enum):
    ADMIN = "admin"
    USER = "user"


class AssetStatus(str, Enum):
    IN_STOCK = "in_stock"
    BORROWED = "borrowed"
    SCRAPPED = "scrapped"


class RequestType(str, Enum):
    EXTEND = "extend"
    SCRAP = "scrap"


class RequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReservationStatus(str, Enum):
    QUEUED = "queued"
    PENDING_CONFIRM = "pending_confirm"
    CONFIRMED = "confirmed"
    BORROWED = "borrowed"
    CANCELLED = "cancelled"
    TIMEOUT_RELEASED = "timeout_released"


class AuditAction(str, Enum):
    BORROW = "borrow"
    RETURN = "return"
    EXTEND_APPROVE = "extend_approve"
    EXTEND_REJECT = "extend_reject"
    SCRAP_APPROVE = "scrap_approve"
    SCRAP_REJECT = "scrap_reject"
    RESERVATION_CREATE = "reservation_create"
    RESERVATION_CONFIRM = "reservation_confirm"
    RESERVATION_CANCEL = "reservation_cancel"
    RESERVATION_TIMEOUT = "reservation_timeout"
    RESTOCK = "restock"
    ASSET_CREATE = "asset_create"


class InventoryStatus(str, Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class DiffType(str, Enum):
    MATCH = "match"
    BORROWED_OCCUPIED = "borrowed_occupied"
    LOSS = "loss"
    OVERAGE = "overage"


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    role = Column(String(20), default=Role.USER)
    created_at = Column(DateTime, default=datetime.now)


class Asset(Base):
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True, index=True)
    asset_code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=False)
    category = Column(String(100))
    total_quantity = Column(Integer, default=1)
    available_quantity = Column(Integer, default=1)
    status = Column(String(20), default=AssetStatus.IN_STOCK)
    location = Column(String(200))
    description = Column(Text)
    last_change_reason = Column(String(500))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    borrow_records = relationship("BorrowRecord", back_populates="asset")


class BorrowRecord(Base):
    __tablename__ = "borrow_records"
    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    borrower_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    quantity = Column(Integer, default=1)
    borrow_date = Column(DateTime, default=datetime.now)
    due_date = Column(DateTime, nullable=False)
    return_date = Column(DateTime)
    status = Column(String(20), default="active")
    purpose = Column(String(500))
    created_at = Column(DateTime, default=datetime.now)
    asset = relationship("Asset", back_populates="borrow_records")
    borrower = relationship("User")


class Request(Base):
    __tablename__ = "requests"
    id = Column(Integer, primary_key=True, index=True)
    request_type = Column(String(20), nullable=False)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    borrow_record_id = Column(Integer, ForeignKey("borrow_records.id"))
    requester_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    approver_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String(20), default=RequestStatus.PENDING)
    extend_days = Column(Integer)
    new_due_date = Column(DateTime)
    reason = Column(String(500))
    created_at = Column(DateTime, default=datetime.now)
    reviewed_at = Column(DateTime)
    asset = relationship("Asset")
    requester = relationship("User", foreign_keys=[requester_id])
    approver = relationship("User", foreign_keys=[approver_id])


class Reservation(Base):
    __tablename__ = "reservations"
    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    requester_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    quantity = Column(Integer, default=1)
    expected_date = Column(DateTime)
    queue_position = Column(Integer, default=0)
    status = Column(String(30), default=ReservationStatus.QUEUED)
    notified_at = Column(DateTime)
    confirm_deadline = Column(DateTime)
    purpose = Column(String(500))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    asset = relationship("Asset")
    requester = relationship("User", foreign_keys=[requester_id])


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(50), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    operator_name = Column(String(100))
    asset_id = Column(Integer, ForeignKey("assets.id"))
    borrow_record_id = Column(Integer, ForeignKey("borrow_records.id"))
    reservation_id = Column(Integer, ForeignKey("reservations.id"))
    request_id = Column(Integer, ForeignKey("requests.id"))
    inventory_id = Column(Integer)
    status_before = Column(String(100))
    status_after = Column(String(100))
    qty_before = Column(Integer)
    qty_after = Column(Integer)
    reason = Column(String(1000))
    related_doc = Column(String(200))
    created_at = Column(DateTime, default=datetime.now)
    operator = relationship("User", foreign_keys=[operator_id])


class InventoryCheck(Base):
    __tablename__ = "inventory_checks"
    id = Column(Integer, primary_key=True, index=True)
    batch_no = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String(30), default=InventoryStatus.DRAFT)
    total_assets = Column(Integer, default=0)
    total_qty_book = Column(Integer, default=0)
    total_qty_actual = Column(Integer, default=0)
    total_qty_borrowed = Column(Integer, default=0)
    total_diff = Column(Integer, default=0)
    remark = Column(String(500))
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    items = relationship("InventoryItem", back_populates="inventory", cascade="all, delete-orphan")
    operator = relationship("User")


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    id = Column(Integer, primary_key=True, index=True)
    inventory_id = Column(Integer, ForeignKey("inventory_checks.id"), nullable=False)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    asset_code = Column(String(50))
    asset_name = Column(String(200))
    qty_book = Column(Integer, default=0)
    qty_actual = Column(Integer, default=0)
    qty_borrowed = Column(Integer, default=0)
    qty_diff = Column(Integer, default=0)
    diff_type = Column(String(30), default=DiffType.MATCH)
    diff_reason = Column(String(500))
    handle_result = Column(String(500))
    created_at = Column(DateTime, default=datetime.now)
    inventory = relationship("InventoryCheck", back_populates="items")
    asset = relationship("Asset")


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    db = SessionLocal()
    try:
        if not db.query(User).first():
            admin = User(username="admin", name="系统管理员", role=Role.ADMIN)
            user1 = User(username="user1", name="张三", role=Role.USER)
            user2 = User(username="user2", name="李四", role=Role.USER)
            db.add_all([admin, user1, user2])
            db.commit()
    finally:
        db.close()


init_db()

app = FastAPI(title="资产借用与归还管理台", version="1.0.0")


def get_current_user(db: Session = Depends(get_db), user_id: int = 1):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def require_admin(user: User = Depends(get_current_user)):
    if user.role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PERMISSION_DENIED", "message": "需要管理员权限", "field": "role"}
        )
    return user


# Pydantic Schemas
class UserOut(BaseModel):
    id: int
    username: str
    name: str
    role: str

    class Config:
        from_attributes = True


class AssetCreate(BaseModel):
    asset_code: str = Field(..., description="资产编号")
    name: str = Field(..., description="资产名称")
    category: Optional[str] = None
    total_quantity: int = Field(1, ge=1, description="入库数量")
    location: Optional[str] = None
    description: Optional[str] = None


class AssetOut(BaseModel):
    id: int
    asset_code: str
    name: str
    category: Optional[str]
    total_quantity: int
    available_quantity: int
    status: str
    location: Optional[str]
    description: Optional[str]
    last_change_reason: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AssetDetailOut(BaseModel):
    asset: AssetOut
    current_holder: Optional[UserOut]
    borrow_history: List[Dict[str, Any]]
    last_change: Optional[Dict[str, Any]]


class BorrowRecordOut(BaseModel):
    id: int
    asset_id: int
    asset_name: str
    borrower_id: int
    borrower_name: str
    quantity: int
    borrow_date: datetime
    due_date: datetime
    return_date: Optional[datetime]
    status: str
    purpose: Optional[str]

    class Config:
        from_attributes = True


class RequestCreate(BaseModel):
    request_type: str
    asset_id: int
    borrow_record_id: Optional[int] = None
    extend_days: Optional[int] = Field(None, ge=1)
    reason: str = Field(..., description="申请原因")


class RequestOut(BaseModel):
    id: int
    request_type: str
    asset_id: int
    asset_name: str
    requester_id: int
    requester_name: str
    status: str
    extend_days: Optional[int]
    new_due_date: Optional[datetime]
    reason: Optional[str]
    created_at: datetime
    reviewed_at: Optional[datetime]

    class Config:
        from_attributes = True


class ReservationCreate(BaseModel):
    asset_id: int = Field(..., description="资产ID")
    requester_id: int = Field(..., description="预约人ID")
    quantity: int = Field(1, ge=1, description="预约数量")
    expected_date: Optional[datetime] = Field(None, description="期望使用日期")
    purpose: Optional[str] = None


class ReservationOut(BaseModel):
    id: int
    asset_id: int
    asset_name: str
    asset_code: str
    requester_id: int
    requester_name: str
    quantity: int
    queue_position: int
    status: str
    expected_date: Optional[datetime]
    notified_at: Optional[datetime]
    confirm_deadline: Optional[datetime]
    purpose: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AuditLogOut(BaseModel):
    id: int
    action: str
    operator_id: int
    operator_name: str
    asset_id: Optional[int]
    asset_name: Optional[str]
    borrow_record_id: Optional[int]
    reservation_id: Optional[int]
    request_id: Optional[int]
    status_before: Optional[str]
    status_after: Optional[str]
    qty_before: Optional[int]
    qty_after: Optional[int]
    reason: Optional[str]
    related_doc: Optional[str]
    created_at: datetime


class InventoryCheckCreate(BaseModel):
    name: str = Field(..., description="盘点批次名称")
    remark: Optional[str] = None


class InventoryCheckOut(BaseModel):
    id: int
    batch_no: str
    name: str
    operator_id: Optional[int]
    operator_name: Optional[str]
    status: str
    total_assets: int
    total_qty_book: int
    total_qty_actual: int
    total_qty_borrowed: int
    total_diff: int
    remark: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime


class InventoryItemUpdate(BaseModel):
    qty_actual: int = Field(..., ge=0, description="实盘数量")
    diff_reason: Optional[str] = None
    handle_result: Optional[str] = None


class InventoryItemOut(BaseModel):
    id: int
    inventory_id: int
    asset_id: int
    asset_code: str
    asset_name: str
    qty_book: int
    qty_actual: int
    qty_borrowed: int
    qty_diff: int
    diff_type: str
    diff_reason: Optional[str]
    handle_result: Optional[str]


CONFIRM_TIMEOUT_MINUTES = 30


def build_error(code: str, message: str, field: Optional[str] = None, details: Optional[Dict] = None):
    err = {"code": code, "message": message}
    if field:
        err["field"] = field
    if details:
        err["details"] = details
    return HTTPException(status_code=400, detail=err)


def write_audit_log(
    db: Session,
    action: AuditAction,
    operator: User,
    asset_id: Optional[int] = None,
    borrow_record_id: Optional[int] = None,
    reservation_id: Optional[int] = None,
    request_id: Optional[int] = None,
    inventory_id: Optional[int] = None,
    status_before: Optional[str] = None,
    status_after: Optional[str] = None,
    qty_before: Optional[int] = None,
    qty_after: Optional[int] = None,
    reason: Optional[str] = None,
    related_doc: Optional[str] = None,
):
    log = AuditLog(
        action=action.value,
        operator_id=operator.id,
        operator_name=operator.name,
        asset_id=asset_id,
        borrow_record_id=borrow_record_id,
        reservation_id=reservation_id,
        request_id=request_id,
        inventory_id=inventory_id,
        status_before=status_before,
        status_after=status_after,
        qty_before=qty_before,
        qty_after=qty_after,
        reason=reason,
        related_doc=related_doc,
    )
    db.add(log)


def recalc_queue_positions(db: Session, asset_id: int):
    queued = db.query(Reservation).filter(
        Reservation.asset_id == asset_id,
        Reservation.status.in_([ReservationStatus.QUEUED, ReservationStatus.PENDING_CONFIRM])
    ).order_by(Reservation.created_at.asc()).all()
    for idx, r in enumerate(queued):
        r.queue_position = idx + 1


def get_reserved_quantity(db: Session, asset_id: int, exclude_reservation_id: Optional[int] = None) -> int:
    """统计某资产 pending_confirm + confirmed 状态预约的总占用件数，用于库存预留。"""
    q = db.query(Reservation).filter(
        Reservation.asset_id == asset_id,
        Reservation.status.in_([ReservationStatus.PENDING_CONFIRM, ReservationStatus.CONFIRMED])
    )
    if exclude_reservation_id is not None:
        q = q.filter(Reservation.id != exclude_reservation_id)
    reserved = q.all()
    return sum(r.quantity for r in reserved)


def get_effective_available_for_borrow(db: Session, asset_id: int, for_reservation_id: Optional[int] = None) -> int:
    """计算"有效可借库存" = 物理可用 - （待确认+已确认预约总占用 - 当前预约自身占用）
    - 普通借出: for_reservation_id=None → 扣除所有已预留的
    - 通过预约借出: for_reservation_id=id → 把自己的占用加回来
    """
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        return 0
    reserved = get_reserved_quantity(db, asset_id, exclude_reservation_id=for_reservation_id)
    return asset.available_quantity - reserved


def process_timeout_reservations(db: Session):
    now = datetime.now()
    pending = db.query(Reservation).filter(
        Reservation.status == ReservationStatus.PENDING_CONFIRM,
        Reservation.confirm_deadline <= now
    ).all()
    affected_asset_ids = set()
    for res in pending:
        old_status = res.status
        res.status = ReservationStatus.TIMEOUT_RELEASED
        asset = db.query(Asset).filter(Asset.id == res.asset_id).first()
        operator = db.query(User).filter(User.id == 1).first()
        if operator is None:
            operator = db.query(User).first()
        write_audit_log(
            db, AuditAction.RESERVATION_TIMEOUT,
            operator if operator else User(name="系统"),
            asset_id=res.asset_id,
            reservation_id=res.id,
            status_before=old_status,
            status_after=ReservationStatus.TIMEOUT_RELEASED,
            qty_before=res.quantity,
            qty_after=0,
            reason=f"预约超时未确认，自动释放 (截止: {res.confirm_deadline.strftime('%Y-%m-%d %H:%M')})",
            related_doc=f"预约单 #{res.id}"
        )
        affected_asset_ids.add(res.asset_id)
    if pending:
        db.commit()
        for aid in affected_asset_ids:
            recalc_queue_positions(db, aid)
            _advance_without_timeout_check(db, aid)
        db.commit()


def _advance_without_timeout_check(db: Session, asset_id: int):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset or asset.status == AssetStatus.SCRAPPED:
        return
    # 关键修复: 推进队列时只能分配"尚未被待确认/已确认预约占用"的净库存
    already_reserved = get_reserved_quantity(db, asset_id)
    available = asset.available_quantity - already_reserved
    if available <= 0:
        return
    queued = db.query(Reservation).filter(
        Reservation.asset_id == asset_id,
        Reservation.status == ReservationStatus.QUEUED
    ).order_by(Reservation.created_at.asc()).all()
    for res in queued:
        if available <= 0:
            break
        if res.quantity <= available:
            res.status = ReservationStatus.PENDING_CONFIRM
            res.notified_at = datetime.now()
            res.confirm_deadline = datetime.now() + timedelta(minutes=CONFIRM_TIMEOUT_MINUTES)
            operator = db.query(User).filter(User.id == 1).first()
            if operator is None:
                operator = db.query(User).first()
            write_audit_log(
                db, AuditAction.RESERVATION_CONFIRM,
                operator if operator else User(name="系统"),
                asset_id=asset_id,
                reservation_id=res.id,
                status_before=ReservationStatus.QUEUED,
                status_after=ReservationStatus.PENDING_CONFIRM,
                qty_before=res.quantity,
                qty_after=res.quantity,
                reason=f"资产归还/入库后通知预约人，进入待确认状态 (确认截止: {res.confirm_deadline.strftime('%Y-%m-%d %H:%M')})",
                related_doc=f"预约单 #{res.id}"
            )
            available -= res.quantity
    recalc_queue_positions(db, asset_id)


def advance_reservation_queue(db: Session, asset_id: int):
    process_timeout_reservations(db)
    _advance_without_timeout_check(db, asset_id)


# User APIs
@app.get("/api/users", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.query(User).all()


@app.get("/api/users/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise build_error("USER_NOT_FOUND", f"用户ID {user_id} 不存在", "user_id")
    return user


# Asset APIs
@app.post("/api/assets", response_model=AssetOut)
def create_asset(data: AssetCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    existing = db.query(Asset).filter(Asset.asset_code == data.asset_code).first()
    if existing:
        raise build_error("DUPLICATE_ASSET_CODE", f"资产编号 {data.asset_code} 已存在", "asset_code")

    asset = Asset(
        asset_code=data.asset_code,
        name=data.name,
        category=data.category,
        total_quantity=data.total_quantity,
        available_quantity=data.total_quantity,
        status=AssetStatus.IN_STOCK,
        location=data.location,
        description=data.description,
        last_change_reason=f"资产入库: {data.total_quantity} 件 (由 {user.name} 操作)"
    )
    db.add(asset)
    db.flush()
    write_audit_log(
        db, AuditAction.ASSET_CREATE, user,
        asset_id=asset.id,
        status_before=None,
        status_after=AssetStatus.IN_STOCK,
        qty_before=0,
        qty_after=data.total_quantity,
        reason=f"新增资产: {data.name}, 入库 {data.total_quantity} 件",
        related_doc=f"资产 #{asset.id} ({data.asset_code})"
    )
    db.commit()
    db.refresh(asset)
    advance_reservation_queue(db, asset.id)
    db.commit()
    db.refresh(asset)
    return asset


@app.get("/api/assets", response_model=List[AssetOut])
def list_assets(db: Session = Depends(get_db), status: Optional[str] = None, keyword: Optional[str] = None):
    q = db.query(Asset)
    if status:
        q = q.filter(Asset.status == status)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter((Asset.name.like(like)) | (Asset.asset_code.like(like)))
    return q.order_by(Asset.updated_at.desc()).all()


@app.get("/api/assets/{asset_id}", response_model=AssetDetailOut)
def get_asset_detail(asset_id: int, db: Session = Depends(get_db)):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise build_error("ASSET_NOT_FOUND", f"资产ID {asset_id} 不存在", "asset_id")

    active_record = db.query(BorrowRecord).filter(
        BorrowRecord.asset_id == asset_id,
        BorrowRecord.status == "active"
    ).first()

    current_holder = None
    if active_record:
        current_holder = db.query(User).filter(User.id == active_record.borrower_id).first()

    records = db.query(BorrowRecord).filter(BorrowRecord.asset_id == asset_id).order_by(BorrowRecord.borrow_date.desc()).all()
    history = []
    for r in records:
        borrower = db.query(User).filter(User.id == r.borrower_id).first()
        overdue = False
        if r.status == "active" and r.due_date < datetime.now():
            overdue = True
        history.append({
            "id": r.id,
            "borrower_id": r.borrower_id,
            "borrower_name": borrower.name if borrower else "未知",
            "quantity": r.quantity,
            "borrow_date": r.borrow_date,
            "due_date": r.due_date,
            "return_date": r.return_date,
            "status": r.status,
            "purpose": r.purpose,
            "overdue": overdue
        })

    last_change = {
        "reason": asset.last_change_reason,
        "at": asset.updated_at
    } if asset.last_change_reason else None

    return AssetDetailOut(
        asset=asset,
        current_holder=UserOut.model_validate(current_holder) if current_holder else None,
        borrow_history=history,
        last_change=last_change
    )


@app.put("/api/assets/{asset_id}/restock", response_model=AssetOut)
def restock_asset(asset_id: int, quantity: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    if quantity <= 0:
        raise build_error("INVALID_QUANTITY", "入库数量必须大于0", "quantity")
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise build_error("ASSET_NOT_FOUND", f"资产ID {asset_id} 不存在", "asset_id")
    if asset.status == AssetStatus.SCRAPPED:
        raise build_error("ASSET_SCRAPPED", "已报废资产无法入库", "asset_id")

    qty_before = asset.available_quantity
    status_before = asset.status
    asset.total_quantity += quantity
    asset.available_quantity += quantity
    if asset.status != AssetStatus.BORROWED:
        asset.status = AssetStatus.IN_STOCK
    asset.last_change_reason = f"补充入库: +{quantity} 件 (由 {user.name} 操作)"
    write_audit_log(
        db, AuditAction.RESTOCK, user,
        asset_id=asset.id,
        status_before=status_before,
        status_after=asset.status,
        qty_before=qty_before,
        qty_after=asset.available_quantity,
        reason=f"补充入库 +{quantity} 件",
        related_doc=f"资产 #{asset.id} ({asset.asset_code})"
    )
    db.commit()
    db.refresh(asset)
    advance_reservation_queue(db, asset.id)
    db.commit()
    db.refresh(asset)
    return asset


# Borrow APIs
class BorrowCreate(BaseModel):
    asset_id: int = Field(..., description="资产ID")
    borrower_id: int = Field(..., description="借用人ID")
    quantity: int = Field(1, ge=1)
    due_date: datetime = Field(..., description="预计归还日期")
    purpose: Optional[str] = None
    reservation_id: Optional[int] = Field(None, description="关联预约ID (已确认预约转借出时使用)")


@app.post("/api/borrow", response_model=BorrowRecordOut)
def borrow_asset(data: BorrowCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    process_timeout_reservations(db)
    asset = db.query(Asset).filter(Asset.id == data.asset_id).first()
    if not asset:
        raise build_error("ASSET_NOT_FOUND", f"资产ID {data.asset_id} 不存在", "asset_id")

    if asset.status == AssetStatus.SCRAPPED:
        raise build_error("ASSET_SCRAPPED", "已报废资产无法借出", "asset_id")

    reservation = None
    if data.reservation_id:
        reservation = db.query(Reservation).filter(Reservation.id == data.reservation_id).first()
        if not reservation:
            raise build_error("RESERVATION_NOT_FOUND", f"预约ID {data.reservation_id} 不存在", "reservation_id")
        if reservation.status != ReservationStatus.CONFIRMED and reservation.status != ReservationStatus.PENDING_CONFIRM:
            raise build_error("RESERVATION_INVALID", f"预约状态为 {reservation.status}，不可用于借出", "reservation_id")
        if reservation.requester_id != data.borrower_id:
            raise build_error("RESERVATION_USER_MISMATCH", "预约人与借用人不一致", "borrower_id")
        if reservation.quantity < data.quantity:
            raise build_error("RESERVATION_QTY_INSUFFICIENT", f"预约数量不足，预约 {reservation.quantity} 件，请求 {data.quantity} 件", "quantity")

    # 关键修复: 统一使用 get_effective_available_for_borrow 计算有效库存
    # - 普通借出: 自动扣除所有 pending_confirm + confirmed 预约的占用
    # - 预约借出 (带 reservation_id): 自动把自己的预约占用加回来
    effective_available = get_effective_available_for_borrow(db, asset.id, for_reservation_id=data.reservation_id)
    reserved_total = get_reserved_quantity(db, asset.id, exclude_reservation_id=data.reservation_id)

    if effective_available < data.quantity:
        detail_payload = {
            "requested": data.quantity,
            "available": effective_available,
            "physical_available": asset.available_quantity,
            "reserved_by_others": reserved_total
        }
        if not data.reservation_id and reserved_total > 0:
            raise build_error(
                "STOCK_RESERVED",
                f"库存已被预约占用: 物理库存 {asset.available_quantity} 件，已被预约预留 {reserved_total} 件，您可借 {effective_available} 件 (建议先发起预约排队)",
                "quantity",
                detail_payload
            )
        raise build_error(
            "INSUFFICIENT_STOCK",
            f"库存不足: 需要 {data.quantity} 件, 实际可借 {effective_available} 件 (物理可用 {asset.available_quantity}, 预约预留 {reserved_total})",
            "quantity",
            detail_payload
        )

    active_record = db.query(BorrowRecord).filter(
        BorrowRecord.asset_id == data.asset_id,
        BorrowRecord.borrower_id == data.borrower_id,
        BorrowRecord.status == "active"
    ).first()
    if active_record:
        raise build_error(
            "DUPLICATE_BORROW",
            f"用户已借用该资产且未归还 (借用记录ID: {active_record.id})",
            "borrower_id",
            {"existing_record_id": active_record.id}
        )

    borrower = db.query(User).filter(User.id == data.borrower_id).first()
    if not borrower:
        raise build_error("USER_NOT_FOUND", f"借用人ID {data.borrower_id} 不存在", "borrower_id")

    if data.due_date <= datetime.now():
        raise build_error("INVALID_DUE_DATE", "归还日期必须晚于当前时间", "due_date")

    qty_before = asset.available_quantity
    status_before = asset.status

    record = BorrowRecord(
        asset_id=data.asset_id,
        borrower_id=data.borrower_id,
        quantity=data.quantity,
        due_date=data.due_date,
        purpose=data.purpose,
        status="active"
    )
    db.add(record)
    db.flush()

    asset.available_quantity -= data.quantity
    if asset.available_quantity <= 0:
        asset.status = AssetStatus.BORROWED
    asset.last_change_reason = f"借出给 {borrower.name}: -{data.quantity} 件, 预计归还 {data.due_date.strftime('%Y-%m-%d')}"

    if reservation:
        old_res_status = reservation.status
        reservation.quantity -= data.quantity
        if reservation.quantity <= 0:
            reservation.status = ReservationStatus.BORROWED
        else:
            reservation.status = ReservationStatus.CONFIRMED
        write_audit_log(
            db, AuditAction.BORROW, user,
            asset_id=asset.id,
            borrow_record_id=record.id,
            reservation_id=reservation.id,
            status_before=status_before,
            status_after=asset.status,
            qty_before=qty_before,
            qty_after=asset.available_quantity,
            reason=f"预约确认后借出: {borrower.name} 取 {data.quantity} 件, 预计归还 {data.due_date.strftime('%Y-%m-%d')}",
            related_doc=f"借用单 #{record.id}, 预约单 #{reservation.id}"
        )
    else:
        write_audit_log(
            db, AuditAction.BORROW, user,
            asset_id=asset.id,
            borrow_record_id=record.id,
            status_before=status_before,
            status_after=asset.status,
            qty_before=qty_before,
            qty_after=asset.available_quantity,
            reason=f"借出: {borrower.name} 取 {data.quantity} 件, 预计归还 {data.due_date.strftime('%Y-%m-%d')}",
            related_doc=f"借用单 #{record.id}"
        )

    db.commit()
    db.refresh(record)
    recalc_queue_positions(db, asset.id)

    return BorrowRecordOut(
        id=record.id,
        asset_id=record.asset_id,
        asset_name=asset.name,
        borrower_id=record.borrower_id,
        borrower_name=borrower.name,
        quantity=record.quantity,
        borrow_date=record.borrow_date,
        due_date=record.due_date,
        return_date=record.return_date,
        status=record.status,
        purpose=record.purpose
    )


@app.post("/api/borrow/{record_id}/return", response_model=BorrowRecordOut)
def return_asset(record_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    process_timeout_reservations(db)
    record = db.query(BorrowRecord).filter(BorrowRecord.id == record_id).first()
    if not record:
        raise build_error("RECORD_NOT_FOUND", f"借用记录ID {record_id} 不存在", "record_id")

    if record.status != "active":
        raise build_error(
            "ALREADY_RETURNED",
            f"该借用记录已处于 {record.status} 状态, 无法重复归还",
            "status",
            {"current_status": record.status}
        )

    asset = db.query(Asset).filter(Asset.id == record.asset_id).first()
    if not asset:
        raise build_error("ASSET_NOT_FOUND", f"关联资产不存在", "asset_id")

    borrower = db.query(User).filter(User.id == record.borrower_id).first()

    overdue = record.due_date < datetime.now()
    qty_before = asset.available_quantity
    status_before = asset.status

    record.return_date = datetime.now()
    record.status = "returned"

    asset.available_quantity += record.quantity
    if asset.status != AssetStatus.SCRAPPED:
        asset.status = AssetStatus.IN_STOCK
    suffix = " (超期归还)" if overdue else ""
    asset.last_change_reason = f"{borrower.name if borrower else '用户'} 归还: +{record.quantity} 件{suffix}"

    reason_text = f"归还: {borrower.name if borrower else '用户'} 返还 {record.quantity} 件{suffix}"
    write_audit_log(
        db, AuditAction.RETURN, user,
        asset_id=asset.id,
        borrow_record_id=record.id,
        status_before=status_before,
        status_after=asset.status,
        qty_before=qty_before,
        qty_after=asset.available_quantity,
        reason=reason_text,
        related_doc=f"借用单 #{record.id}"
    )
    db.commit()
    db.refresh(record)
    advance_reservation_queue(db, asset.id)
    db.commit()
    db.refresh(record)

    return BorrowRecordOut(
        id=record.id,
        asset_id=record.asset_id,
        asset_name=asset.name,
        borrower_id=record.borrower_id,
        borrower_name=borrower.name if borrower else "未知",
        quantity=record.quantity,
        borrow_date=record.borrow_date,
        due_date=record.due_date,
        return_date=record.return_date,
        status=record.status,
        purpose=record.purpose
    )


@app.get("/api/borrow", response_model=List[BorrowRecordOut])
def list_borrow_records(db: Session = Depends(get_db), status: Optional[str] = None, borrower_id: Optional[int] = None, overdue_only: bool = False):
    q = db.query(BorrowRecord)
    if status:
        q = q.filter(BorrowRecord.status == status)
    if borrower_id:
        q = q.filter(BorrowRecord.borrower_id == borrower_id)
    records = q.order_by(BorrowRecord.borrow_date.desc()).all()

    result = []
    for r in records:
        if overdue_only and not (r.status == "active" and r.due_date < datetime.now()):
            continue
        asset = db.query(Asset).filter(Asset.id == r.asset_id).first()
        borrower = db.query(User).filter(User.id == r.borrower_id).first()
        result.append(BorrowRecordOut(
            id=r.id,
            asset_id=r.asset_id,
            asset_name=asset.name if asset else "未知",
            borrower_id=r.borrower_id,
            borrower_name=borrower.name if borrower else "未知",
            quantity=r.quantity,
            borrow_date=r.borrow_date,
            due_date=r.due_date,
            return_date=r.return_date,
            status=r.status,
            purpose=r.purpose
        ))
    return result


# Request APIs
@app.post("/api/requests", response_model=RequestOut)
def create_request(data: RequestCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    asset = db.query(Asset).filter(Asset.id == data.asset_id).first()
    if not asset:
        raise build_error("ASSET_NOT_FOUND", f"资产ID {data.asset_id} 不存在", "asset_id")

    if data.request_type == RequestType.EXTEND:
        if not data.borrow_record_id:
            raise build_error("MISSING_BORROW_RECORD", "延期申请需要指定借用记录ID", "borrow_record_id")
        if not data.extend_days or data.extend_days <= 0:
            raise build_error("INVALID_EXTEND_DAYS", "延期天数必须大于0", "extend_days")

        record = db.query(BorrowRecord).filter(BorrowRecord.id == data.borrow_record_id).first()
        if not record:
            raise build_error("RECORD_NOT_FOUND", f"借用记录ID {data.borrow_record_id} 不存在", "borrow_record_id")
        if record.status != "active":
            raise build_error("RECORD_NOT_ACTIVE", "仅可对未归还的借用申请延期", "borrow_record_id")
        if record.borrower_id != user.id and user.role != Role.ADMIN:
            raise build_error("PERMISSION_DENIED", "仅借用人或管理员可申请延期", "requester_id")

        pending = db.query(Request).filter(
            Request.borrow_record_id == data.borrow_record_id,
            Request.request_type == RequestType.EXTEND,
            Request.status == RequestStatus.PENDING
        ).first()
        if pending:
            raise build_error("PENDING_REQUEST_EXISTS", f"该借用已有待审核的延期申请 (ID: {pending.id})", "borrow_record_id")

        new_due = record.due_date + timedelta(days=data.extend_days)
        req = Request(
            request_type=RequestType.EXTEND,
            asset_id=data.asset_id,
            borrow_record_id=data.borrow_record_id,
            requester_id=user.id,
            extend_days=data.extend_days,
            new_due_date=new_due,
            reason=data.reason
        )
        db.add(req)
        db.commit()
        db.refresh(req)

    elif data.request_type == RequestType.SCRAP:
        if user.role != Role.ADMIN:
            raise build_error("PERMISSION_DENIED", "仅管理员可提交报废申请", "requester_id")

        if asset.status == AssetStatus.SCRAPPED:
            raise build_error("ALREADY_SCRAPPED", "该资产已报废", "asset_id")

        pending = db.query(Request).filter(
            Request.asset_id == data.asset_id,
            Request.request_type == RequestType.SCRAP,
            Request.status == RequestStatus.PENDING
        ).first()
        if pending:
            raise build_error("PENDING_REQUEST_EXISTS", f"该资产已有待审核的报废申请 (ID: {pending.id})", "asset_id")

        active_borrows = db.query(BorrowRecord).filter(
            BorrowRecord.asset_id == data.asset_id,
            BorrowRecord.status == "active"
        ).count()
        if active_borrows > 0:
            raise build_error("ASSET_IN_USE", f"该资产有 {active_borrows} 件仍在借用中, 无法申请报废", "asset_id")

        req = Request(
            request_type=RequestType.SCRAP,
            asset_id=data.asset_id,
            requester_id=user.id,
            reason=data.reason
        )
        db.add(req)
        db.commit()
        db.refresh(req)
    else:
        raise build_error("INVALID_REQUEST_TYPE", f"不支持的申请类型: {data.request_type}", "request_type")

    return RequestOut(
        id=req.id,
        request_type=req.request_type,
        asset_id=req.asset_id,
        asset_name=asset.name,
        requester_id=req.requester_id,
        requester_name=user.name,
        status=req.status,
        extend_days=req.extend_days,
        new_due_date=req.new_due_date,
        reason=req.reason,
        created_at=req.created_at,
        reviewed_at=req.reviewed_at
    )


@app.get("/api/requests", response_model=List[RequestOut])
def list_requests(db: Session = Depends(get_db), status: Optional[str] = None, request_type: Optional[str] = None):
    q = db.query(Request)
    if status:
        q = q.filter(Request.status == status)
    if request_type:
        q = q.filter(Request.request_type == request_type)
    reqs = q.order_by(Request.created_at.desc()).all()

    result = []
    for r in reqs:
        asset = db.query(Asset).filter(Asset.id == r.asset_id).first()
        requester = db.query(User).filter(User.id == r.requester_id).first()
        result.append(RequestOut(
            id=r.id,
            request_type=r.request_type,
            asset_id=r.asset_id,
            asset_name=asset.name if asset else "未知",
            requester_id=r.requester_id,
            requester_name=requester.name if requester else "未知",
            status=r.status,
            extend_days=r.extend_days,
            new_due_date=r.new_due_date,
            reason=r.reason,
            created_at=r.created_at,
            reviewed_at=r.reviewed_at
        ))
    return result


@app.post("/api/requests/{request_id}/approve", response_model=RequestOut)
def approve_request(request_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    req = db.query(Request).filter(Request.id == request_id).first()
    if not req:
        raise build_error("REQUEST_NOT_FOUND", f"申请ID {request_id} 不存在", "request_id")
    if req.status != RequestStatus.PENDING:
        raise build_error("INVALID_STATUS", f"仅待审核申请可审批, 当前状态: {req.status}", "status")

    asset = db.query(Asset).filter(Asset.id == req.asset_id).first()
    requester = db.query(User).filter(User.id == req.requester_id).first()

    if req.request_type == RequestType.EXTEND:
        record = db.query(BorrowRecord).filter(BorrowRecord.id == req.borrow_record_id).first()
        if not record:
            raise build_error("RECORD_NOT_FOUND", "关联借用记录不存在", "borrow_record_id")
        if record.status != "active":
            raise build_error("RECORD_NOT_ACTIVE", "借用记录已归还, 延期无效", "borrow_record_id")

        old_due = record.due_date
        record.due_date = req.new_due_date
        asset.last_change_reason = f"延期申请通过: 归还日期延至 {req.new_due_date.strftime('%Y-%m-%d')} (审批人: {user.name})"
        write_audit_log(
            db, AuditAction.EXTEND_APPROVE, user,
            asset_id=asset.id if asset else None,
            borrow_record_id=record.id,
            request_id=req.id,
            status_before=f"due:{old_due.strftime('%Y-%m-%d')}",
            status_after=f"due:{req.new_due_date.strftime('%Y-%m-%d')}",
            qty_before=record.quantity,
            qty_after=record.quantity,
            reason=f"延期通过 +{req.extend_days}天: {req.reason}",
            related_doc=f"申请 #{req.id}, 借用单 #{record.id}"
        )

    elif req.request_type == RequestType.SCRAP:
        old_status = asset.status
        old_qty = asset.available_quantity
        asset.status = AssetStatus.SCRAPPED
        asset.available_quantity = 0
        asset.last_change_reason = f"资产报废: {req.reason} (审批人: {user.name})"
        write_audit_log(
            db, AuditAction.SCRAP_APPROVE, user,
            asset_id=asset.id,
            request_id=req.id,
            status_before=old_status,
            status_after=AssetStatus.SCRAPPED,
            qty_before=old_qty,
            qty_after=0,
            reason=f"报废通过: {req.reason}",
            related_doc=f"申请 #{req.id}"
        )

    req.status = RequestStatus.APPROVED
    req.approver_id = user.id
    req.reviewed_at = datetime.now()
    db.commit()
    db.refresh(req)

    return RequestOut(
        id=req.id,
        request_type=req.request_type,
        asset_id=req.asset_id,
        asset_name=asset.name if asset else "未知",
        requester_id=req.requester_id,
        requester_name=requester.name if requester else "未知",
        status=req.status,
        extend_days=req.extend_days,
        new_due_date=req.new_due_date,
        reason=req.reason,
        created_at=req.created_at,
        reviewed_at=req.reviewed_at
    )


@app.post("/api/requests/{request_id}/reject", response_model=RequestOut)
def reject_request(request_id: int, reject_reason: str, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    req = db.query(Request).filter(Request.id == request_id).first()
    if not req:
        raise build_error("REQUEST_NOT_FOUND", f"申请ID {request_id} 不存在", "request_id")
    if req.status != RequestStatus.PENDING:
        raise build_error("INVALID_STATUS", f"仅待审核申请可审批, 当前状态: {req.status}", "status")

    asset = db.query(Asset).filter(Asset.id == req.asset_id).first()
    requester = db.query(User).filter(User.id == req.requester_id).first()

    req.status = RequestStatus.REJECTED
    req.approver_id = user.id
    req.reviewed_at = datetime.now()
    req.reason = f"{req.reason} (拒绝原因: {reject_reason})"

    if req.request_type == RequestType.EXTEND:
        action = AuditAction.EXTEND_REJECT
    else:
        action = AuditAction.SCRAP_REJECT
    write_audit_log(
        db, action, user,
        asset_id=asset.id if asset else None,
        request_id=req.id,
        borrow_record_id=req.borrow_record_id,
        status_before=RequestStatus.PENDING,
        status_after=RequestStatus.REJECTED,
        reason=f"申请拒绝: {reject_reason}",
        related_doc=f"申请 #{req.id}"
    )

    db.commit()
    db.refresh(req)

    return RequestOut(
        id=req.id,
        request_type=req.request_type,
        asset_id=req.asset_id,
        asset_name=asset.name if asset else "未知",
        requester_id=req.requester_id,
        requester_name=requester.name if requester else "未知",
        status=req.status,
        extend_days=req.extend_days,
        new_due_date=req.new_due_date,
        reason=req.reason,
        created_at=req.created_at,
        reviewed_at=req.reviewed_at
    )


# Reservation APIs
@app.post("/api/reservations", response_model=ReservationOut)
def create_reservation(data: ReservationCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    process_timeout_reservations(db)
    asset = db.query(Asset).filter(Asset.id == data.asset_id).first()
    if not asset:
        raise build_error("ASSET_NOT_FOUND", f"资产ID {data.asset_id} 不存在", "asset_id")
    if asset.status == AssetStatus.SCRAPPED:
        raise build_error("ASSET_SCRAPPED", "已报废资产不可预约", "asset_id")

    requester = db.query(User).filter(User.id == data.requester_id).first()
    if not requester:
        raise build_error("USER_NOT_FOUND", f"预约人ID {data.requester_id} 不存在", "requester_id")

    existing_active = db.query(Reservation).filter(
        Reservation.asset_id == data.asset_id,
        Reservation.requester_id == data.requester_id,
        Reservation.status.in_([ReservationStatus.QUEUED, ReservationStatus.PENDING_CONFIRM, ReservationStatus.CONFIRMED])
    ).first()
    if existing_active:
        raise build_error(
            "DUPLICATE_RESERVATION",
            f"该用户已有有效预约 (预约ID: {existing_active.id})",
            "requester_id",
            {"existing_reservation_id": existing_active.id}
        )

    active_borrow = db.query(BorrowRecord).filter(
        BorrowRecord.asset_id == data.asset_id,
        BorrowRecord.borrower_id == data.requester_id,
        BorrowRecord.status == "active"
    ).first()
    if active_borrow:
        raise build_error(
            "ACTIVE_BORROW_EXISTS",
            f"该用户已借用该资产且未归还 (借用记录ID: {active_borrow.id})",
            "requester_id"
        )

    if data.expected_date and data.expected_date <= datetime.now():
        raise build_error("INVALID_EXPECTED_DATE", "期望使用日期必须晚于当前时间", "expected_date")

    reservation = Reservation(
        asset_id=data.asset_id,
        requester_id=data.requester_id,
        quantity=data.quantity,
        expected_date=data.expected_date,
        purpose=data.purpose,
        status=ReservationStatus.QUEUED
    )
    db.add(reservation)
    db.flush()
    recalc_queue_positions(db, data.asset_id)

    write_audit_log(
        db, AuditAction.RESERVATION_CREATE, user,
        asset_id=data.asset_id,
        reservation_id=reservation.id,
        status_before=None,
        status_after=ReservationStatus.QUEUED,
        qty_before=0,
        qty_after=data.quantity,
        reason=f"发起预约: 数量 {data.quantity} 件" + (f", 期望日期 {data.expected_date.strftime('%Y-%m-%d')}" if data.expected_date else ""),
        related_doc=f"预约单 #{reservation.id}"
    )

    db.commit()
    db.refresh(reservation)
    advance_reservation_queue(db, data.asset_id)
    db.commit()
    db.refresh(reservation)
    recalc_queue_positions(db, data.asset_id)
    db.commit()
    db.refresh(reservation)

    return ReservationOut(
        id=reservation.id,
        asset_id=asset.id,
        asset_name=asset.name,
        asset_code=asset.asset_code,
        requester_id=reservation.requester_id,
        requester_name=requester.name,
        quantity=reservation.quantity,
        queue_position=reservation.queue_position,
        status=reservation.status,
        expected_date=reservation.expected_date,
        notified_at=reservation.notified_at,
        confirm_deadline=reservation.confirm_deadline,
        purpose=reservation.purpose,
        created_at=reservation.created_at,
        updated_at=reservation.updated_at
    )


@app.get("/api/reservations", response_model=List[ReservationOut])
def list_reservations(
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    asset_id: Optional[int] = None,
    requester_id: Optional[int] = None
):
    process_timeout_reservations(db)
    q = db.query(Reservation)
    if status:
        q = q.filter(Reservation.status == status)
    if asset_id:
        q = q.filter(Reservation.asset_id == asset_id)
    if requester_id:
        q = q.filter(Reservation.requester_id == requester_id)
    reservations = q.order_by(Reservation.created_at.desc()).all()

    result = []
    for r in reservations:
        asset = db.query(Asset).filter(Asset.id == r.asset_id).first()
        requester = db.query(User).filter(User.id == r.requester_id).first()
        result.append(ReservationOut(
            id=r.id,
            asset_id=r.asset_id,
            asset_name=asset.name if asset else "未知",
            asset_code=asset.asset_code if asset else "",
            requester_id=r.requester_id,
            requester_name=requester.name if requester else "未知",
            quantity=r.quantity,
            queue_position=r.queue_position,
            status=r.status,
            expected_date=r.expected_date,
            notified_at=r.notified_at,
            confirm_deadline=r.confirm_deadline,
            purpose=r.purpose,
            created_at=r.created_at,
            updated_at=r.updated_at
        ))
    return result


@app.post("/api/reservations/{reservation_id}/confirm", response_model=ReservationOut)
def confirm_reservation(reservation_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    process_timeout_reservations(db)
    res = db.query(Reservation).filter(Reservation.id == reservation_id).first()
    if not res:
        raise build_error("RESERVATION_NOT_FOUND", f"预约ID {reservation_id} 不存在", "reservation_id")
    if res.status not in [ReservationStatus.PENDING_CONFIRM, ReservationStatus.QUEUED]:
        raise build_error(
            "RESERVATION_INVALID_STATUS",
            f"仅排队中或待确认状态可确认, 当前状态: {res.status}",
            "status"
        )

    asset = db.query(Asset).filter(Asset.id == res.asset_id).first()
    requester = db.query(User).filter(User.id == res.requester_id).first()

    if res.status == ReservationStatus.QUEUED:
        effective = get_effective_available_for_borrow(db, asset.id)
        reserved = get_reserved_quantity(db, asset.id)
        if effective < res.quantity:
            raise build_error(
                "INSUFFICIENT_STOCK",
                f"库存不足: 需要 {res.quantity} 件, 实际可借 {effective} 件 (物理可用 {asset.available_quantity}, 已被预约预留 {reserved})",
                "quantity",
                {"requested": res.quantity, "available": effective, "physical_available": asset.available_quantity, "reserved_by_others": reserved}
            )

    old_status = res.status
    res.status = ReservationStatus.CONFIRMED

    write_audit_log(
        db, AuditAction.RESERVATION_CONFIRM, user,
        asset_id=res.asset_id,
        reservation_id=res.id,
        status_before=old_status,
        status_after=ReservationStatus.CONFIRMED,
        qty_before=res.quantity,
        qty_after=res.quantity,
        reason="预约人确认预约，资产已为其预留",
        related_doc=f"预约单 #{res.id}"
    )

    db.commit()
    db.refresh(res)
    recalc_queue_positions(db, res.asset_id)
    db.commit()
    db.refresh(res)

    return ReservationOut(
        id=res.id,
        asset_id=asset.id,
        asset_name=asset.name,
        asset_code=asset.asset_code,
        requester_id=res.requester_id,
        requester_name=requester.name,
        quantity=res.quantity,
        queue_position=res.queue_position,
        status=res.status,
        expected_date=res.expected_date,
        notified_at=res.notified_at,
        confirm_deadline=res.confirm_deadline,
        purpose=res.purpose,
        created_at=res.created_at,
        updated_at=res.updated_at
    )


@app.post("/api/reservations/{reservation_id}/cancel", response_model=ReservationOut)
def cancel_reservation(reservation_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    process_timeout_reservations(db)
    res = db.query(Reservation).filter(Reservation.id == reservation_id).first()
    if not res:
        raise build_error("RESERVATION_NOT_FOUND", f"预约ID {reservation_id} 不存在", "reservation_id")
    if res.status in [ReservationStatus.CANCELLED, ReservationStatus.TIMEOUT_RELEASED, ReservationStatus.BORROWED]:
        raise build_error(
            "RESERVATION_INVALID_STATUS",
            f"当前状态 {res.status} 不可取消",
            "status"
        )
    if res.requester_id != user.id and user.role != Role.ADMIN:
        raise build_error("PERMISSION_DENIED", "仅预约人或管理员可取消预约", "requester_id")

    asset = db.query(Asset).filter(Asset.id == res.asset_id).first()
    requester = db.query(User).filter(User.id == res.requester_id).first()

    old_status = res.status
    res.status = ReservationStatus.CANCELLED

    write_audit_log(
        db, AuditAction.RESERVATION_CANCEL, user,
        asset_id=res.asset_id,
        reservation_id=res.id,
        status_before=old_status,
        status_after=ReservationStatus.CANCELLED,
        qty_before=res.quantity,
        qty_after=0,
        reason="预约取消",
        related_doc=f"预约单 #{res.id}"
    )

    db.commit()
    db.refresh(res)
    advance_reservation_queue(db, res.asset_id)
    db.commit()
    db.refresh(res)

    return ReservationOut(
        id=res.id,
        asset_id=asset.id,
        asset_name=asset.name,
        asset_code=asset.asset_code,
        requester_id=res.requester_id,
        requester_name=requester.name,
        quantity=res.quantity,
        queue_position=res.queue_position,
        status=res.status,
        expected_date=res.expected_date,
        notified_at=res.notified_at,
        confirm_deadline=res.confirm_deadline,
        purpose=res.purpose,
        created_at=res.created_at,
        updated_at=res.updated_at
    )


# Audit Log APIs
@app.get("/api/audit-logs", response_model=List[AuditLogOut])
def list_audit_logs(
    db: Session = Depends(get_db),
    action: Optional[str] = None,
    asset_id: Optional[int] = None,
    operator_id: Optional[int] = None,
    limit: int = Query(200, ge=1, le=5000)
):
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if asset_id:
        q = q.filter(AuditLog.asset_id == asset_id)
    if operator_id:
        q = q.filter(AuditLog.operator_id == operator_id)
    logs = q.order_by(AuditLog.created_at.desc()).limit(limit).all()

    result = []
    for log in logs:
        asset = None
        if log.asset_id:
            asset = db.query(Asset).filter(Asset.id == log.asset_id).first()
        result.append(AuditLogOut(
            id=log.id,
            action=log.action,
            operator_id=log.operator_id,
            operator_name=log.operator_name or "未知",
            asset_id=log.asset_id,
            asset_name=asset.name if asset else None,
            borrow_record_id=log.borrow_record_id,
            reservation_id=log.reservation_id,
            request_id=log.request_id,
            status_before=log.status_before,
            status_after=log.status_after,
            qty_before=log.qty_before,
            qty_after=log.qty_after,
            reason=log.reason,
            related_doc=log.related_doc,
            created_at=log.created_at
        ))
    return result


# Inventory Check APIs
def gen_batch_no():
    now = datetime.now()
    return f"PD-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}"


def compute_diff_type(qty_book: int, qty_actual: int, qty_borrowed: int):
    expected_on_hand = qty_book - qty_borrowed
    diff = qty_actual - expected_on_hand
    if diff == 0:
        return DiffType.MATCH, 0
    elif diff < 0:
        return DiffType.LOSS, diff
    else:
        return DiffType.OVERAGE, diff


@app.post("/api/inventory", response_model=InventoryCheckOut)
def create_inventory(data: InventoryCheckCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    batch = InventoryCheck(
        batch_no=gen_batch_no(),
        name=data.name,
        operator_id=user.id,
        status=InventoryStatus.DRAFT,
        remark=data.remark
    )
    db.add(batch)
    db.flush()

    assets = db.query(Asset).filter(Asset.status != AssetStatus.SCRAPPED).all()
    for asset in assets:
        borrowed_qty = db.query(BorrowRecord).filter(
            BorrowRecord.asset_id == asset.id,
            BorrowRecord.status == "active"
        ).all()
        total_borrowed = sum(r.quantity for r in borrowed_qty)
        qty_book = asset.total_quantity - sum(
            (r.quantity for r in db.query(BorrowRecord).filter(
                BorrowRecord.asset_id == asset.id,
                BorrowRecord.status == "returned"
            ).all()), 0
        )
        qty_book = asset.total_quantity
        item = InventoryItem(
            inventory_id=batch.id,
            asset_id=asset.id,
            asset_code=asset.asset_code,
            asset_name=asset.name,
            qty_book=qty_book,
            qty_actual=qty_book - total_borrowed,
            qty_borrowed=total_borrowed,
            qty_diff=0,
            diff_type=DiffType.MATCH
        )
        db.add(item)
    batch.total_assets = len(assets)
    batch.total_qty_book = sum(a.total_quantity for a in assets)
    batch.total_qty_actual = batch.total_qty_book
    batch.total_qty_borrowed = sum(
        db.query(BorrowRecord).filter(BorrowRecord.asset_id == a.id, BorrowRecord.status == "active").all()
        and sum(r.quantity for r in db.query(BorrowRecord).filter(
            BorrowRecord.asset_id == a.id, BorrowRecord.status == "active"
        ).all()) or 0
        for a in assets
    )
    db.commit()
    db.refresh(batch)

    operator = db.query(User).filter(User.id == batch.operator_id).first()
    return InventoryCheckOut(
        id=batch.id,
        batch_no=batch.batch_no,
        name=batch.name,
        operator_id=batch.operator_id,
        operator_name=operator.name if operator else None,
        status=batch.status,
        total_assets=batch.total_assets,
        total_qty_book=batch.total_qty_book,
        total_qty_actual=batch.total_qty_actual,
        total_qty_borrowed=batch.total_qty_borrowed,
        total_diff=batch.total_diff,
        remark=batch.remark,
        started_at=batch.started_at,
        completed_at=batch.completed_at,
        created_at=batch.created_at
    )


@app.post("/api/inventory/{inventory_id}/start", response_model=InventoryCheckOut)
def start_inventory(inventory_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    batch = db.query(InventoryCheck).filter(InventoryCheck.id == inventory_id).first()
    if not batch:
        raise build_error("INVENTORY_NOT_FOUND", f"盘点批次ID {inventory_id} 不存在", "inventory_id")
    if batch.status != InventoryStatus.DRAFT:
        raise build_error("INVALID_STATUS", f"仅草稿状态可启动, 当前状态: {batch.status}", "status")
    batch.status = InventoryStatus.IN_PROGRESS
    batch.started_at = datetime.now()
    db.commit()
    db.refresh(batch)
    operator = db.query(User).filter(User.id == batch.operator_id).first()
    return InventoryCheckOut(
        id=batch.id,
        batch_no=batch.batch_no,
        name=batch.name,
        operator_id=batch.operator_id,
        operator_name=operator.name if operator else None,
        status=batch.status,
        total_assets=batch.total_assets,
        total_qty_book=batch.total_qty_book,
        total_qty_actual=batch.total_qty_actual,
        total_qty_borrowed=batch.total_qty_borrowed,
        total_diff=batch.total_diff,
        remark=batch.remark,
        started_at=batch.started_at,
        completed_at=batch.completed_at,
        created_at=batch.created_at
    )


@app.get("/api/inventory", response_model=List[InventoryCheckOut])
def list_inventory(db: Session = Depends(get_db), status: Optional[str] = None):
    q = db.query(InventoryCheck)
    if status:
        q = q.filter(InventoryCheck.status == status)
    batches = q.order_by(InventoryCheck.created_at.desc()).all()
    result = []
    for b in batches:
        operator = db.query(User).filter(User.id == b.operator_id).first()
        result.append(InventoryCheckOut(
            id=b.id,
            batch_no=b.batch_no,
            name=b.name,
            operator_id=b.operator_id,
            operator_name=operator.name if operator else None,
            status=b.status,
            total_assets=b.total_assets,
            total_qty_book=b.total_qty_book,
            total_qty_actual=b.total_qty_actual,
            total_qty_borrowed=b.total_qty_borrowed,
            total_diff=b.total_diff,
            remark=b.remark,
            started_at=b.started_at,
            completed_at=b.completed_at,
            created_at=b.created_at
        ))
    return result


@app.get("/api/inventory/{inventory_id}/items", response_model=List[InventoryItemOut])
def get_inventory_items(inventory_id: int, db: Session = Depends(get_db)):
    batch = db.query(InventoryCheck).filter(InventoryCheck.id == inventory_id).first()
    if not batch:
        raise build_error("INVENTORY_NOT_FOUND", f"盘点批次ID {inventory_id} 不存在", "inventory_id")
    items = db.query(InventoryItem).filter(InventoryItem.inventory_id == inventory_id).order_by(InventoryItem.id.asc()).all()
    result = []
    for it in items:
        result.append(InventoryItemOut(
            id=it.id,
            inventory_id=it.inventory_id,
            asset_id=it.asset_id,
            asset_code=it.asset_code,
            asset_name=it.asset_name,
            qty_book=it.qty_book,
            qty_actual=it.qty_actual,
            qty_borrowed=it.qty_borrowed,
            qty_diff=it.qty_diff,
            diff_type=it.diff_type,
            diff_reason=it.diff_reason,
            handle_result=it.handle_result
        ))
    return result


@app.put("/api/inventory/{inventory_id}/items/{item_id}", response_model=InventoryItemOut)
def update_inventory_item(
    inventory_id: int,
    item_id: int,
    data: InventoryItemUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin)
):
    batch = db.query(InventoryCheck).filter(InventoryCheck.id == inventory_id).first()
    if not batch:
        raise build_error("INVENTORY_NOT_FOUND", f"盘点批次ID {inventory_id} 不存在", "inventory_id")
    if batch.status == InventoryStatus.COMPLETED:
        raise build_error("INVENTORY_COMPLETED", "已完成的盘点不可修改", "status")

    item = db.query(InventoryItem).filter(InventoryItem.id == item_id, InventoryItem.inventory_id == inventory_id).first()
    if not item:
        raise build_error("ITEM_NOT_FOUND", f"盘点明细ID {item_id} 不存在", "item_id")

    item.qty_actual = data.qty_actual
    item.diff_reason = data.diff_reason
    item.handle_result = data.handle_result

    expected_on_hand = item.qty_book - item.qty_borrowed
    qty_diff = data.qty_actual - expected_on_hand
    item.qty_diff = qty_diff
    if qty_diff == 0:
        item.diff_type = DiffType.MATCH
    elif qty_borrowed_condition := (item.qty_borrowed > 0 and data.qty_actual == expected_on_hand):
        item.diff_type = DiffType.BORROWED_OCCUPIED
    elif qty_diff < 0:
        item.diff_type = DiffType.LOSS
    else:
        item.diff_type = DiffType.OVERAGE
    if item.qty_borrowed > 0 and data.qty_actual == expected_on_hand:
        item.diff_type = DiffType.BORROWED_OCCUPIED
    elif qty_diff == 0:
        item.diff_type = DiffType.MATCH
    elif qty_diff < 0:
        item.diff_type = DiffType.LOSS
    else:
        item.diff_type = DiffType.OVERAGE

    all_items = db.query(InventoryItem).filter(InventoryItem.inventory_id == inventory_id).all()
    batch.total_qty_actual = sum(i.qty_actual for i in all_items)
    batch.total_diff = sum(i.qty_diff for i in all_items)

    db.commit()
    db.refresh(item)

    return InventoryItemOut(
        id=item.id,
        inventory_id=item.inventory_id,
        asset_id=item.asset_id,
        asset_code=item.asset_code,
        asset_name=item.asset_name,
        qty_book=item.qty_book,
        qty_actual=item.qty_actual,
        qty_borrowed=item.qty_borrowed,
        qty_diff=item.qty_diff,
        diff_type=item.diff_type,
        diff_reason=item.diff_reason,
        handle_result=item.handle_result
    )


@app.post("/api/inventory/{inventory_id}/complete", response_model=InventoryCheckOut)
def complete_inventory(inventory_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    batch = db.query(InventoryCheck).filter(InventoryCheck.id == inventory_id).first()
    if not batch:
        raise build_error("INVENTORY_NOT_FOUND", f"盘点批次ID {inventory_id} 不存在", "inventory_id")
    if batch.status != InventoryStatus.IN_PROGRESS:
        raise build_error("INVALID_STATUS", f"仅进行中状态可完成, 当前状态: {batch.status}", "status")
    batch.status = InventoryStatus.COMPLETED
    batch.completed_at = datetime.now()
    db.commit()
    db.refresh(batch)
    operator = db.query(User).filter(User.id == batch.operator_id).first()
    return InventoryCheckOut(
        id=batch.id,
        batch_no=batch.batch_no,
        name=batch.name,
        operator_id=batch.operator_id,
        operator_name=operator.name if operator else None,
        status=batch.status,
        total_assets=batch.total_assets,
        total_qty_book=batch.total_qty_book,
        total_qty_actual=batch.total_qty_actual,
        total_qty_borrowed=batch.total_qty_borrowed,
        total_diff=batch.total_diff,
        remark=batch.remark,
        started_at=batch.started_at,
        completed_at=batch.completed_at,
        created_at=batch.created_at
    )


# Enhanced Export API
@app.get("/api/export/ledger")
def export_ledger(db: Session = Depends(get_db)):
    process_timeout_reservations(db)
    records = db.query(BorrowRecord).order_by(BorrowRecord.borrow_date.desc()).all()
    reservations = db.query(Reservation).order_by(Reservation.created_at.desc()).all()
    audit_logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(1000).all()
    inventories = db.query(InventoryCheck).filter(InventoryCheck.status == InventoryStatus.COMPLETED).order_by(InventoryCheck.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["=" * 80])
    writer.writerow(["资产借用台账 (完整导出)"])
    writer.writerow([f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
    writer.writerow([])

    writer.writerow(["=" * 80])
    writer.writerow(["【一、借用记录】"])
    writer.writerow([
        "记录ID", "资产编号", "资产名称", "借用人ID", "借用人姓名",
        "借用数量", "借用时间", "应归还时间", "实际归还时间",
        "状态", "是否超期", "借用目的"
    ])
    for r in records:
        asset = db.query(Asset).filter(Asset.id == r.asset_id).first()
        borrower = db.query(User).filter(User.id == r.borrower_id).first()
        overdue = "否"
        if r.status == "active" and r.due_date < datetime.now():
            overdue = "是"
        elif r.return_date and r.return_date > r.due_date:
            overdue = "是"
        writer.writerow([
            r.id,
            asset.asset_code if asset else "",
            asset.name if asset else "",
            r.borrower_id,
            borrower.name if borrower else "",
            r.quantity,
            r.borrow_date.strftime("%Y-%m-%d %H:%M:%S") if r.borrow_date else "",
            r.due_date.strftime("%Y-%m-%d %H:%M:%S") if r.due_date else "",
            r.return_date.strftime("%Y-%m-%d %H:%M:%S") if r.return_date else "",
            "借用中" if r.status == "active" else "已归还",
            overdue,
            r.purpose or ""
        ])
    writer.writerow([])

    writer.writerow(["=" * 80])
    writer.writerow(["【二、预约队列】"])
    writer.writerow([
        "预约ID", "资产编号", "资产名称", "预约人ID", "预约人姓名",
        "预约数量", "排队位置", "状态", "期望使用日期",
        "通知时间", "确认截止", "创建时间", "备注目的"
    ])
    res_status_map = {
        "queued": "排队中", "pending_confirm": "待确认", "confirmed": "已确认",
        "borrowed": "已借出", "cancelled": "已取消", "timeout_released": "超时释放"
    }
    for r in reservations:
        asset = db.query(Asset).filter(Asset.id == r.asset_id).first()
        requester = db.query(User).filter(User.id == r.requester_id).first()
        writer.writerow([
            r.id,
            asset.asset_code if asset else "",
            asset.name if asset else "",
            r.requester_id,
            requester.name if requester else "",
            r.quantity,
            r.queue_position,
            res_status_map.get(r.status, r.status),
            r.expected_date.strftime("%Y-%m-%d %H:%M:%S") if r.expected_date else "",
            r.notified_at.strftime("%Y-%m-%d %H:%M:%S") if r.notified_at else "",
            r.confirm_deadline.strftime("%Y-%m-%d %H:%M:%S") if r.confirm_deadline else "",
            r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
            r.purpose or ""
        ])
    writer.writerow([])

    writer.writerow(["=" * 80])
    writer.writerow(["【三、审计日志】"])
    writer.writerow([
        "日志ID", "操作类型", "操作人", "资产", "关联单据",
        "操作前状态", "操作后状态", "操作前数量", "操作后数量",
        "变更原因", "操作时间"
    ])
    action_map = {
        "borrow": "借出", "return": "归还",
        "extend_approve": "延期审批通过", "extend_reject": "延期审批拒绝",
        "scrap_approve": "报废审批通过", "scrap_reject": "报废审批拒绝",
        "reservation_create": "创建预约", "reservation_confirm": "预约确认",
        "reservation_cancel": "预约取消", "reservation_timeout": "预约超时释放",
        "restock": "补充入库", "asset_create": "新增资产"
    }
    for log in audit_logs:
        asset = db.query(Asset).filter(Asset.id == log.asset_id).first() if log.asset_id else None
        writer.writerow([
            log.id,
            action_map.get(log.action, log.action),
            log.operator_name or "未知",
            f"{asset.asset_code}-{asset.name}" if asset else "",
            log.related_doc or "",
            log.status_before or "",
            log.status_after or "",
            log.qty_before if log.qty_before is not None else "",
            log.qty_after if log.qty_after is not None else "",
            log.reason or "",
            log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else ""
        ])
    writer.writerow([])

    writer.writerow(["=" * 80])
    writer.writerow(["【四、盘点差异摘要】"])
    for batch in inventories:
        operator = db.query(User).filter(User.id == batch.operator_id).first()
        writer.writerow([
            f"盘点批次: {batch.batch_no}",
            f"名称: {batch.name}",
            f"状态: 已完成",
            f"操作人: {operator.name if operator else '未知'}"
        ])
        writer.writerow([
            f"资产数: {batch.total_assets}",
            f"账面总数量: {batch.total_qty_book}",
            f"实盘总数量: {batch.total_qty_actual}",
            f"借出占用: {batch.total_qty_borrowed}",
            f"净差异: {batch.total_diff}"
        ])
        writer.writerow(["明细ID", "资产编号", "资产名称", "账面数", "借出占用", "应在库数",
                         "实盘数", "差异数", "差异类型", "差异原因", "处理结果"])
        diff_map = {
            "match": "账实相符", "borrowed_occupied": "借出占用(正常)",
            "loss": "盘亏异常", "overage": "盘盈异常"
        }
        items = db.query(InventoryItem).filter(InventoryItem.inventory_id == batch.id).all()
        for it in items:
            expected = it.qty_book - it.qty_borrowed
            writer.writerow([
                it.id, it.asset_code, it.asset_name,
                it.qty_book, it.qty_borrowed, expected,
                it.qty_actual, it.qty_diff,
                diff_map.get(it.diff_type, it.diff_type),
                it.diff_reason or "",
                it.handle_result or ""
            ])
        writer.writerow([])
    writer.writerow([])
    writer.writerow(["=" * 80])
    writer.writerow(["导出结束"])
    writer.writerow(["=" * 80])

    output.seek(0)
    headers = {
        "Content-Disposition": f"attachment; filename=asset_ledger_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    }
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8-sig",
        headers=headers
    )


# Statistics
@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    process_timeout_reservations(db)
    total_assets = db.query(Asset).count()
    in_stock = db.query(Asset).filter(Asset.status == AssetStatus.IN_STOCK).count()
    borrowed = db.query(Asset).filter(Asset.status == AssetStatus.BORROWED).count()
    scrapped = db.query(Asset).filter(Asset.status == AssetStatus.SCRAPPED).count()
    total_users = db.query(User).count()

    active_records = db.query(BorrowRecord).filter(BorrowRecord.status == "active").all()
    total_borrowed = sum(r.quantity for r in active_records)
    overdue_count = sum(1 for r in active_records if r.due_date < datetime.now())

    pending_requests = db.query(Request).filter(Request.status == RequestStatus.PENDING).count()

    queued_reservations = db.query(Reservation).filter(
        Reservation.status == ReservationStatus.QUEUED
    ).count()
    pending_confirm = db.query(Reservation).filter(
        Reservation.status == ReservationStatus.PENDING_CONFIRM
    ).count()
    total_reservations = db.query(Reservation).count()

    total_audit_logs = db.query(AuditLog).count()
    pending_inventories = db.query(InventoryCheck).filter(
        InventoryCheck.status.in_([InventoryStatus.DRAFT, InventoryStatus.IN_PROGRESS])
    ).count()

    return {
        "total_assets": total_assets,
        "total_users": total_users,
        "in_stock": in_stock,
        "borrowed": borrowed,
        "scrapped": scrapped,
        "active_borrows": len(active_records),
        "total_borrowed": total_borrowed,
        "overdue_count": overdue_count,
        "pending_requests": pending_requests,
        "queued_reservations": queued_reservations,
        "pending_confirm": pending_confirm,
        "total_reservations": total_reservations,
        "total_audit_logs": total_audit_logs,
        "pending_inventories": pending_inventories
    }


# Frontend
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: FastAPIRequest):
    return templates.TemplateResponse("index.html", {"request": request})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

import csv
import io
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request as FastAPIRequest
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Boolean
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


class BorrowCreate(BaseModel):
    asset_id: int = Field(..., description="资产ID")
    borrower_id: int = Field(..., description="借用人ID")
    quantity: int = Field(1, ge=1)
    due_date: datetime = Field(..., description="预计归还日期")
    purpose: Optional[str] = None


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


# Helper functions
def build_error(code: str, message: str, field: Optional[str] = None, details: Optional[Dict] = None):
    err = {"code": code, "message": message}
    if field:
        err["field"] = field
    if details:
        err["details"] = details
    return HTTPException(status_code=400, detail=err)


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

    asset.total_quantity += quantity
    asset.available_quantity += quantity
    if asset.status != AssetStatus.BORROWED:
        asset.status = AssetStatus.IN_STOCK
    asset.last_change_reason = f"补充入库: +{quantity} 件 (由 {user.name} 操作)"
    db.commit()
    db.refresh(asset)
    return asset


# Borrow APIs
@app.post("/api/borrow", response_model=BorrowRecordOut)
def borrow_asset(data: BorrowCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    asset = db.query(Asset).filter(Asset.id == data.asset_id).first()
    if not asset:
        raise build_error("ASSET_NOT_FOUND", f"资产ID {data.asset_id} 不存在", "asset_id")

    if asset.status == AssetStatus.SCRAPPED:
        raise build_error("ASSET_SCRAPPED", "已报废资产无法借出", "asset_id")

    if asset.available_quantity < data.quantity:
        raise build_error(
            "INSUFFICIENT_STOCK",
            f"库存不足: 需要 {data.quantity} 件, 可用 {asset.available_quantity} 件",
            "quantity",
            {"requested": data.quantity, "available": asset.available_quantity}
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

    record = BorrowRecord(
        asset_id=data.asset_id,
        borrower_id=data.borrower_id,
        quantity=data.quantity,
        due_date=data.due_date,
        purpose=data.purpose,
        status="active"
    )
    db.add(record)

    asset.available_quantity -= data.quantity
    if asset.available_quantity <= 0:
        asset.status = AssetStatus.BORROWED
    asset.last_change_reason = f"借出给 {borrower.name}: -{data.quantity} 件, 预计归还 {data.due_date.strftime('%Y-%m-%d')}"
    db.commit()
    db.refresh(record)

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
    record.return_date = datetime.now()
    record.status = "returned"

    asset.available_quantity += record.quantity
    if asset.status != AssetStatus.SCRAPPED:
        asset.status = AssetStatus.IN_STOCK
    suffix = " (超期归还)" if overdue else ""
    asset.last_change_reason = f"{borrower.name if borrower else '用户'} 归还: +{record.quantity} 件{suffix}"
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

        record.due_date = req.new_due_date
        asset.last_change_reason = f"延期申请通过: 归还日期延至 {req.new_due_date.strftime('%Y-%m-%d')} (审批人: {user.name})"

    elif req.request_type == RequestType.SCRAP:
        asset.status = AssetStatus.SCRAPPED
        asset.available_quantity = 0
        asset.last_change_reason = f"资产报废: {req.reason} (审批人: {user.name})"

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


# Export API
@app.get("/api/export/ledger")
def export_ledger(db: Session = Depends(get_db)):
    records = db.query(BorrowRecord).order_by(BorrowRecord.borrow_date.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
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
    total_assets = db.query(Asset).count()
    in_stock = db.query(Asset).filter(Asset.status == AssetStatus.IN_STOCK).count()
    borrowed = db.query(Asset).filter(Asset.status == AssetStatus.BORROWED).count()
    scrapped = db.query(Asset).filter(Asset.status == AssetStatus.SCRAPPED).count()

    active_records = db.query(BorrowRecord).filter(BorrowRecord.status == "active").all()
    overdue_count = sum(1 for r in active_records if r.due_date < datetime.now())

    pending_requests = db.query(Request).filter(Request.status == RequestStatus.PENDING).count()

    return {
        "total_assets": total_assets,
        "in_stock": in_stock,
        "borrowed": borrowed,
        "scrapped": scrapped,
        "active_borrows": len(active_records),
        "overdue_count": overdue_count,
        "pending_requests": pending_requests
    }


# Frontend
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: FastAPIRequest):
    return templates.TemplateResponse("index.html", {"request": request})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

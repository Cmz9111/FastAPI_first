#ORM（Object-RelationalMapping 对象关系映射) - 建表以及路由
import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from sqlalchemy import DateTime, func, String, Float, select
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker,AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped,mapped_column



#1.创建异步引擎
ASYNC_DATABASE_URL = "mysql+aiomysql://root:123456@localhost:3306/fastapi_first?charset=utf8mb4"
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=True, #可选,输出SQL日志
    pool_size=10, #设置连接池活跃的连接数
    max_overflow=20 #允许额外的连接数
)

#2.定义模型类: 基类 + 表对应的模型类
#基类:创建时间、更新时间; 书籍表:id、书名、作者、价格、出版社
class Base(DeclarativeBase):
    create_time: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        insert_default=func.now,
        comment="创建时间",
    )
    update_time: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        insert_default=func.now,
        onupdate=func.now,
        comment="修改时间",
    )

class Book(Base):
    __tablename__ = "book"

    id: Mapped[int] = mapped_column(primary_key=True,comment="书籍id")
    bookname:Mapped[str] = mapped_column(String(255),comment="书名")
    author:Mapped[str] = mapped_column(String(255),comment="作者")
    price:Mapped[float] = mapped_column(Float,comment="价格")
    publisher:Mapped[str] = mapped_column(String(255),comment="出版社")

#3.建表:定义函数建表 -> FastAPI 启动的时候调用建表的函数
async def create_table():
    #获取异步引擎,创建事务 - 建表
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all) #Base模型类的元数据创建

# @app.on_event("startup")
# async def startup_event():
#     await create_table()

# 4. 用 lifespan 管理启动 / 关闭事件（替代已弃用的 @app.on_event("startup")）
@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_table()        # yield 之前 = startup
    yield
    await async_engine.dispose()  # yield 之后 = shutdown，释放连接池

app = FastAPI(lifespan=lifespan)

#需求: 查询功能的接口,查询图书 -> 依赖注入:创建依赖项获取数据库会话 + Depends 注入路由处理函数
#通过工厂函数async_sessionmaker设计会话工厂
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine, #绑定数据库引擎
    class_=AsyncSession, #指定会话类
    expire_on_commit=False #提交后会话不过期,不会重新查询数据库
)

#依赖项
async def get_database():
    async with AsyncSessionLocal() as session:
        try:
            yield session #返回数据库会话给路由处理函数
            await session.commit() #提交事务
        except Exception:
            await session.rollback() #有异常,回滚
            raise
        finally:
            await session.close()

@app.get("/book/books")
async def get_book_list(db: AsyncSession = Depends(get_database)):
    #查询
    result = await db.execute(select(Book))
    book = result.scalars().all()
    return book
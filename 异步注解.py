"""
main.py 的逐行注释版 —— 只关注一件事：
        为什么这套代码必须写成 async / await / async with / yield 的样子。

阅读顺序建议：
    第 0 节（为什么全都要 async）
    → 第 2 节（async with 的第一处）
    → 第 3 节（yield 的第一处：应用生命周期）
    → 第 4 节（yield 的第二处：请求级数据库会话）
    → 第 5 节（await：真正的查库）
    → 第 6 节（这份代码的隐患清单）
"""
import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from sqlalchemy import DateTime, func, String, Float, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# =============================================================================
# 0. 先回答最根本的问题：为什么整套都要 async？
# =============================================================================
#
# 这个接口干的事是：收到请求 → 查 MySQL → 返回。
# 一次查库大约几毫秒到几十毫秒，这段时间 CPU 什么事都不用做，只是在"等网络回包"。
#
#   同步写法（Flask / Django 传统视图 / FastAPI 里的 def 路由）：
#       等待期间，这个线程 100% 被占用，什么别的都干不了。
#       想同时处理 1000 个请求 → 就得开 1000 个线程 → 内存爆炸 + 上下文切换拖垮 CPU。
#
#   异步写法（本文件）：
#       等待期间，协程主动"让出"CPU（await），事件循环转头去处理别的请求。
#       一个线程就能同时"挂"着成百上千个请求。
#
# 结论：IO 密集型场景（查库、调外部 HTTP、读写文件）是 async 的主战场。
#       注意反面：如果你做的是大量 CPU 计算，async 帮不上忙，那要用多进程。
#
# 正因为选了 async，下面每一处都被迫"传染"成了 async：
#   驱动要异步 → aiomysql（而不是 pymysql）
#   引擎要异步 → create_async_engine
#   会话要异步 → AsyncSession
#   建表/取连接/提交/关闭 全都要 await
#   所以路由函数也必须是 async def
# 这就是常说的 "async 传染性 / 红蓝药丸"：一旦有一处是 async，调用链上全得是 async。


# =============================================================================
# 1. 异步引擎：连接池的"池子"
# =============================================================================
# 注意 mysql+aiomysql:// —— 驱动是 aiomysql（异步版 MySQL 驱动）。
# 如果写成 mysql+pymysql://，就配不了 create_async_engine，整条异步链断在这里。
ASYNC_DATABASE_URL = "mysql+aiomysql://root:123456@localhost:3306/fastapi_first?charset=utf8mb4"

async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=True,          # 打印每条 SQL，学习阶段很有用，生产环境务必关掉
    pool_size=10,       # 池子里常驻 10 条连接
    max_overflow=20,    # 高峰时最多再临时开 20 条，用完销毁
)
# 为什么要连接池？因为"建立 TCP 连接 + MySQL 握手 + 鉴权"很贵（几十毫秒）。
# 池子让连接复用，请求来了直接拿一条现成的。
#
# 关键认知：这一行执行完，一条连接都还没建。引擎是「惰性」的，
# 真正建连接发生在第一次 await 拿连接的时候（第 4 节）。


# =============================================================================
# 2. 模型类（和 async 无关，只是表结构的 Python 描述）
# =============================================================================
class Base(DeclarativeBase):
    create_time: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        insert_default=func.now,     # 插入时由数据库填 NOW()
        comment="创建时间",
    )
    update_time: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        insert_default=func.now,
        onupdate=func.now,           # 更新时由数据库刷新 NOW()
        comment="修改时间",
    )


class Book(Base):
    __tablename__ = "book"

    id: Mapped[int] = mapped_column(primary_key=True, comment="书籍id")
    bookname: Mapped[str] = mapped_column(String(255), comment="书名")
    author: Mapped[str] = mapped_column(String(255), comment="作者")
    price: Mapped[float] = mapped_column(Float, comment="价格")
    publisher: Mapped[str] = mapped_column(String(255), comment="出版社")


# =============================================================================
# 3. 建表 —— 第一处 async with
# =============================================================================
async def create_table():
    #                        ┌──────────────────────────────────────────────┐
    #   async with ... as conn│ 这里发生了三件事：                            │
    #                        │ ① await 从连接池借一条连接（IO，可挂起）        │
    #                        │ ② 在这条连接上开一个事务                       │
    #                        │ ③ 代码块结束后自动 commit，出错则 rollback      │
    #                        └──────────────────────────────────────────────┘
    async with async_engine.begin() as conn:
        # run_sync：SQLAlchemy 的 DDL（create_all）是「同步 API」，
        # SQLAlchemy 会把它丢进一个 greenlet/线程里跑，外面用 await 等它完成。
        # 这是「异步世界调用同步代码」的标准桥接手段。
        await conn.run_sync(Base.metadata.create_all)


# =============================================================================
# 4. lifespan —— 第一处 yield：管的是「整个应用的一生」
# =============================================================================
# @asynccontextmanager 把「async def + 里面有 yield」的函数，
# 变成一个可以被 async with 使用的异步上下文管理器。
# 也就是说，下面这个函数等价于实现了 __aenter__ / __aexit__ 的对象：
#
#       yield 之前  →  __aenter__  →  启动阶段
#       yield 处    →  暂停，把控制权交出去
#       yield 之后  →  __aexit__   →  关闭阶段
@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_table()            # ← 启动：建表。yield 之前的代码只跑一次
    yield                           # ← 应用在这里"挂起"，接下来几小时都在服务请求
    await async_engine.dispose()    # ← 关闭：Ctrl+C 时才跑，优雅关掉连接池里所有连接


# 为什么不用 return？
#   因为 return 会让函数直接结束，dispose() 立刻执行 —— 连接池刚建好就被关。
#   只有 yield 才能做到「先做一半，交出控制权，等别人说结束了，我再回来做另一半」。
#
# 为什么不用被弃用的 @app.on_event("startup") / ("shutdown")？
#   因为它把「启动」和「关闭」拆成两个独立函数，两边的资源没法共享变量
#   （比如启动时开的连接池，shutdown 函数里拿不到）。
#   lifespan 把它们写在同一个函数里，靠 yield 天然分隔，资源共享、异常可控。

app = FastAPI(lifespan=lifespan)


# =============================================================================
# 5. 会话工厂 + 依赖 —— 第二处 yield：管的是「一次请求的一生」
# =============================================================================
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,        # 会话从哪个引擎（连接池）拿连接
    class_=AsyncSession,      # 产出异步会话
    expire_on_commit=False,   # ★ 异步下必须这么设，原因见下
)

# ★ expire_on_commit=False 为什么在异步里几乎是强制的？
#   默认 True 的行为：commit 之后，会话里所有 ORM 对象被标记为"过期"，
#   下次访问 obj.bookname 时，ORM 会「自动再发一条 SELECT 去刷新」。
#   这在同步代码里很方便，但在异步里是灾难：
#       —— 那个自动刷新是一次 IO，却没有 await 的机会（你只是写了个属性访问），
#          于是直接报 MissingGreenlet 错误。
#   所以在异步 ORM 中，一律 expire_on_commit=False，
#   需要什么字段就在查询里显式查出来，绝不依赖「隐式懒加载」。


# 依赖项：FastAPI 允许依赖函数用 yield，语义是：
#       yield 之前 → 请求处理之前执行（准备资源）
#       yield 的值 → 注入给路由函数
#       yield 之后 → 响应发出之后执行（清理资源）
async def get_database():
    async with AsyncSessionLocal() as session:   # 借连接（await __aenter__）
        try:
            yield session                        # ← 把会话交给路由，函数在此冻结
            await session.commit()               # 路由没抛异常 → 提交事务
        except Exception:
            await session.rollback()             # 路由抛异常 → 回滚
            raise                                # ★ 必须继续抛出，否则 500 会被吞成 200
        finally:
            await session.close()                # 无论如何都要关


# 为什么这里必须用 yield，不能写成 return session？
#   试想写成：
#       async def get_database():
#           async with AsyncSessionLocal() as session:
#               return session          # 函数到这就结束了
#   一旦 return，函数栈立刻销毁，async with 退出、连接被立刻还回池子。
#   路由函数拿到的就是一个「已经关掉的会话」，查询直接失败。
#
#   yield 的价值：它是「暂停」而不是「结束」。
#   函数现场（session 变量、async with 的上下文）被完整冻结在内存里，
#   等 FastAPI 处理完请求、发完响应，再回来从 yield 的下一行继续 ——
#   这时候才 commit、才 close。资源生命周期正好 == 一次请求的生命周期。
#
# 这就是「依赖注入 + yield」这个组合存在的全部理由：
#   用最自然的代码形状，表达「给我一个横跨整个请求的资源」。


# =============================================================================
# 6. 路由 —— await 登场：真正发生 IO 的地方
# =============================================================================
@app.get("/book/books")
async def get_book_list(db: AsyncSession = Depends(get_database)):
    # Depends(get_database) 告诉 FastAPI：
    #   "先去跑 get_database()，把它 yield 出来的东西塞进 db 参数"
    result = await db.execute(select(Book))   # ← 唯一的 IO 点：协程在此挂起
    book = result.scalars().all()             # ← 纯 CPU 的内存操作，不需要 await
    return book


# await db.execute(...) 这一瞬间发生了什么？
#   ① 协程在此冻结，控制权交还事件循环
#   ② 事件循环转头去处理别的请求（这就是并发的来源）
#   ③ MySQL 回包后，事件循环把这个协程唤醒，从这一行继续
#   ④ result 拿到数据
#
# 如果这里写成了同步的 db.execute（忘了 await）：
#   要么返回个协程对象没执行，要么直接阻塞整个事件循环 —— 所有请求一起卡死。


# =============================================================================
# 7. 这份代码的隐患清单（学完关键字后，你应该能看出这些问题）
# =============================================================================
#
# ① finally 里的 session.close() 是多余的
#    async with AsyncSessionLocal() 退出时本身就会 close。
#    不过显式写出来无害，属于防御性写法，可以先留着。
#
# ② 路由没有 response_model
#    return book 直接丢出 SQLAlchemy 对象列表，FastAPI 只能靠猜测去序列化，
#    字段丢了/类型错了都不知道。应该建一个 Pydantic 的 BookOut 并声明：
#        @app.get("/book/books", response_model=list[BookOut])
#    另外：在 async 路由里返回 ORM 对象后，Pydantic 序列化时可能触发懒加载 → 报错。
#    稳妥做法是在路由内就转成 Pydantic 对象或 dict。
#
# ③ 每次启动都 create_table()
#    学习阶段没问题；生产环境必须换成 Alembic 迁移，
#    否则模型改了字段，已存在的表不会被 ALTER，而且多副本部署时会并发建表。
#
# ④ 明文密码写在 URL 里
#    应该读环境变量：os.getenv("DB_URL")。
#
# ⑤ pool_size=10 + max_overflow=20 → 单进程最多 30 条连接
#    用 uvicorn --workers 4 起 4 个进程就是 120 条。
#    要保证 单进程上限 × worker 数 < MySQL 的 max_connections。
#
# ⑥ echo=True 生产环境一定要关，否则每条 SQL 都打日志，性能与磁盘双杀。
#
# ⑦ 缺少分页：select(Book) 全表查，数据一大就炸。
#    应加 .limit(...).offset(...) 或游标分页。
#
# ⑧ 没有启动入口。开发时可以在文件末尾加：
#        if __name__ == "__main__":
#            import uvicorn
#            uvicorn.run("main:app", reload=True)
#    生产用：uvicorn main:app --workers 4

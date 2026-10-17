# FastAPI + SQLAlchemy 异步 ORM 逐行讲解

> 源文件：`C:\Users\ax\PycharmProjects\FastAPI_first\main.py`
> 技术栈：FastAPI + SQLAlchemy 2.0 异步 ORM + MySQL(aiomysql)

---

## 0. 先看全局：这个文件的执行顺序

```
① 导入模块（只定义名字，不连接数据库）
② 创建异步引擎 async_engine   ← 此时还"没有"真正连数据库（惰性）
③ 定义 ORM 模型 Base / Book    ← 只是 Python 类描述，生成 MetaData（表结构蓝图）
④ 定义 create_table()          ← 还没执行
⑤ 定义 lifespan()              ← 还没执行
⑥ app = FastAPI(lifespan=...)  ← 注册生命周期，仍未连库
⑦ 创建会话工厂 AsyncSessionLocal
⑧ 定义依赖 get_database()
⑨ 注册路由 @app.get("/book/books")
---------------------------------------------------------------
【uvicorn 启动】→ ASGI lifespan startup → ⑥ 的 lifespan 跑起来
   → 建表 create_table()
   → yield（应用开始接收请求）
【请求进来】→ 依赖 get_database() 产出 session → 路由执行查询 → 返回响应
   → 依赖收尾：commit / rollback → close
【Ctrl+C】→ lifespan shutdown → async_engine.dispose()
```

关键认知：**模块顶层代码只是"搭好架子"，真正的 I/O（建连接、建表）都发生在事件循环启动之后**。

---

## 1. 导入语句（1–8 行）

```python
import datetime                                             # ①
from contextlib import asynccontextmanager                  # ②

from fastapi import FastAPI, Depends                        # ③
from sqlalchemy import DateTime, func, String, Float, select # ④
from sqlalchemy.ext.asyncio import (                        # ⑤
    create_async_engine, async_sessionmaker, AsyncSession
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column  # ⑥
```

| 行 | 导入项 | 用途 |
|---|---|---|
| ① | `datetime` | 标准库。仅为类型注解服务：`Mapped[datetime.datetime]` 让 SQLAlchemy 推导列类型；也提醒你这列存的是"日期时间"而非字符串。 |
| ② | `asynccontextmanager` | 把"含 `yield` 的 async generator 函数"包装成异步上下文管理器。它是实现 `lifespan` 的语法基础（`@app.on_event` 的官方替代方案）。 |
| ③ | `FastAPI` / `Depends` | 应用实例；`Depends` 声明"这个参数由某个依赖函数提供"，是 FastAPI 依赖注入系统的入口。 |
| ④ | `DateTime, func, String, Float, select` | `DateTime/String/Float` 是**列类型**（对应 SQL 的 DATETIME/VARCHAR/FLOAT）；`func` 生成 SQL 函数表达式（如 `func.now()` → `NOW()`）；`select` 是 SQLAlchemy 2.0 风格的 SELECT 构造器（取代 1.x 的 `session.query()`）。 |
| ⑤ | `create_async_engine, async_sessionmaker, AsyncSession` | 异步三件套：引擎（连接池 + dialect）、会话工厂、异步会话类。 |
| ⑥ | `DeclarativeBase, Mapped, mapped_column` | 2.0 声明式建模三件套：基类、`Mapped[...]` 类型注解容器、`mapped_column()` 列构造器。 |

> 背景：`sqlalchemy.ext.asyncio` 是 **asyncio 适配层**。SQLAlchemy 的核心（SQL 编译、ORM）仍是同步代码，官方文档称之为 "IO 之外的部分"；异步 API 通过 **greenlet** 在同步核心与 `await` 之间做上下文切换。理解这一点，你就能明白后面为什么会出现 `run_sync()` 这种"把同步函数丢进线程"的写法。

---

## 2. 异步引擎（12–19 行）

```python
ASYNC_DATABASE_URL = "mysql+aiomysql://root:123456@localhost:3306/fastapi_first?charset=utf8mb4"  # ①
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=True,        # ②
    pool_size=10,     # ③
    max_overflow=20,  # ④
)
```

### ① URL 拆解
```
mysql+aiomysql://root:123456@localhost:3306/fastapi_first?charset=utf8mb4
 └─dialect┘└driver┘   └user┘└pwd┘  └host┘ └port┘└database┘ └连接参数┘
```
- `mysql` 是 SQL 方言（决定生成什么 SQL），`aiomysql` 是**异步驱动**（真正发网络包的库，基于 PyMySQL）。
- 方言和驱动分离：换 `pymysql`（同步）或 `asyncmy`（异步）只需换 URL，模型代码不动。
- `charset=utf8mb4`：MySQL 里 `utf8` 是残废的三字节版本，**必须**用 `utf8mb4` 才能存 emoji 和生僻字。

### ② `echo=True`
把执行的每一条 SQL 语句及其参数输出到名为 `sqlalchemy.engine.Engine` 的 logger（默认走 stderr/标准日志）。
- 价值：学习/调试时能看到 ORM 到底发了什么 SQL。
- 代价：有字符串渲染开销 + 日志量巨大，**生产环境应改为 `False`**（或按环境变量开关）。还有 `echo_pool=True` 可单独观察连接池借还。

### ③ `pool_size=10`
**常驻连接池大小**。引擎会维护最多 10 条"长期持有、用完归还、不会立刻关闭"的 TCP 连接（默认 5）。
- 好处：避免每次请求都做 TCP 三次握手 + MySQL 认证（认证是昂贵的 RSA/散列过程）。
- 注意：这些连接是**并发上限的基线**，不是"越多越好"——MySQL 的 `max_connections` 是整个实例共享的。

### ④ `max_overflow=20`
在 `pool_size` 用满后，**允许临时再创建** 20 条连接。
- 这些"溢出连接"用完即关，**不归还到池里**。
- 所以本配置的**瞬时最大并发连接数 = 10 + 20 = 30**。
- 超过 30 的请求不会报错，而是**排队等待**，等待时长上限 `pool_timeout`（默认 30 秒），超时抛 `QueuePool limit of size 10 overflow 20 reached` 类异常。
- 调优直觉：`pool_size` 覆盖常态并发，`max_overflow` 吸收突发流量。

### 本段未设置但很重要的参数（改进点）
| 参数 | 建议 | 原因 |
|---|---|---|
| `pool_pre_ping=True` | 强烈建议开启 | 每次借出连接前发一个轻量 `SELECT 1` 探活，避免拿到已被服务端断开的"僵尸连接" |
| `pool_recycle=3600` | 建议 | MySQL `wait_timeout` 默认 8 小时会单方面切断空闲连接，`pool_recycle` 让池主动淘汰老连接 |
| `pool_timeout` | 可调 | 默认 30s，想要的快速失败可设小 |

---

## 3. 模型定义（21–43 行）

### 3.1 公共基类 Base

```python
class Base(DeclarativeBase):        # ①
    create_time: Mapped[datetime.datetime] = mapped_column(   # ②
        DateTime,
        insert_default=func.now,    # ③
        comment="创建时间",          # ④
    )
    update_time: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        insert_default=func.now,    # ③
        onupdate=func.now,          # ⑤
        comment="修改时间",
    )
```

**① `DeclarativeBase`（SQLAlchemy 2.0）**
- 取代 1.x 的 `declarative_base()` 函数调用式写法。
- 继承它就获得三样东西：`metadata`（表结构蓝图容器）、`registry`（类 ↔ 表的映射注册表，也是 `Mapped[]` 注解解析的依据）、以及 declarative 的元类行为。
- 副作用：这里定义的 `create_time / update_time` 会成为 **所有子模型的公共列**（相当于一个 mixin）。若某张表不想要，应改用独立 mixin 类而非塞进 Base。

**② `Mapped[T]` + `mapped_column()`**
- `Mapped[datetime.datetime]` 是**注解式类型容器**：告诉 SQLAlchemy 这一列的 Python 类型是 `datetime`；SQLAlchemy 会据此**自动推导** SQL 类型（datetime → `DateTime`）。
- 因此 `mapped_column(DateTime)` 里的 `DateTime` 其实是"显式重复声明"，写不写都能工作，写上更直白。
- `Mapped[str]` → 可空性由注解决定：`Mapped[str]` = NOT NULL；`Mapped[str | None]`（或 `Optional[str]`）= NULL 允许。这是 2.0 相比 1.x 的一大进步：**类型注解即 schema**。
- `mapped_column()` 内部就是构造 `Column` 对象，只是与注解联动、可省略类型。

**③ `insert_default=func.now`（重点，最易混淆的三个 default）**

| 参数 | 执行位置 | 谁计算 | 是否写进 `CREATE TABLE` |
|---|---|---|---|
| `default=值/函数` | Python 侧 | Python（ORM 在生成 INSERT 前算好，作为绑定参数发送） | 否 |
| `insert_default=SQL表达式` | SQL 侧（语句内联） | **数据库**（渲染成 `INSERT ... VALUES (..., now())`） | 否 |
| `server_default=SQL表达式` | SQL 侧（表定义） | **数据库**（DDL 里的 `DEFAULT now()` 兜底） | **是** |

- `insert_default` 是 SQLAlchemy **2.0 新增**的参数：把默认值作为 SQL 表达式内联到 INSERT 语句中，由数据库计算。相比 `default`（Python 计算）的好处是：多进程/多机部署时，时间基准统一为**数据库服务器时间**，不会出现各机器时钟漂移。
- 传 `func.now`（函数对象，未调用）或 `func.now()`（调用后得到 SQL 函数表达式）**通常都能工作**；官方文档示例多写作 `func.now()`，语义更明确，建议统一写 `func.now()`。
- 与 `server_default` 的差异要牢记：**没有 server_default，表结构里就没有 DEFAULT 约束**。如果别的程序（命令行、ETL 脚本）绕过 ORM 直接 INSERT 且不写时间字段，得到的是 NULL。

**④ `comment="创建时间"`**
列注释，MySQL/PostgreSQL 支持，会渲染成 DDL 的 `COMMENT '创建时间'`。纯元数据，不影响行为，但对"数据库自解释"非常有用（`SHOW FULL COLUMNS FROM book` 可见）。表级注释可用 `__table_args__ = {"comment": "书籍表"}`。

**⑤ `onupdate=func.now`**
执行 UPDATE 时，自动把该列设为 `now()`，即 `SET update_time = now()`。
- 注意：`onupdate` 只在 **SQLAlchemy 发出的 UPDATE** 中生效（本例是 SQL 侧表达式）；裸 SQL 或别的程序更新不会触发。若要数据库兜底，需要 `ON UPDATE CURRENT_TIMESTAMP`（MySQL 特有，可配合 `server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")`）。
- 想用 Python 侧版本就写 `onupdate=datetime.datetime.now`（不加括号，传可调用对象）。

**类型建议**：`DateTime` 默认不带时区（naive）。跨时区系统建议统一存 UTC；MySQL 的 `DATETIME` 不保存时区信息，`TIMESTAMP` 会做时区转换。

### 3.2 实体模型 Book

```python
class Book(Base):
    __tablename__ = "book"                                              # ①

    id: Mapped[int] = mapped_column(primary_key=True, comment="书籍id")   # ②
    bookname: Mapped[str] = mapped_column(String(255), comment="书名")    # ③
    author: Mapped[str] = mapped_column(String(255), comment="作者")
    price: Mapped[float] = mapped_column(Float, comment="价格")           # ④
    publisher: Mapped[str] = mapped_column(String(255), comment="出版社")
```

- **① `__tablename__`**：必填，指定物理表名；不写 SQLAlchemy 不会自动生成。
- **② `primary_key=True`**：主键。`Mapped[int]` 推导为 `Integer`，在 MySQL 下 SQLAlchemy 对整型主键默认加 `AUTO_INCREMENT`（`autoincrement="auto"` 行为）。这里甚至没写 `Integer`，再次体现注解推导列类型的能力。
- **③ `String(255)`**：`VARCHAR(255)`。**必须给长度**——MySQL 不允许无长度的 VARCHAR。255 也是 MySQL 索引长度的安全边界（utf8mb4 下 255×4=1020 字节 < 3072 限制）。
- **④ `Float`**：对应 MySQL `FLOAT`。**金额强烈不建议用 Float**（二进制浮点有精度误差，0.1+0.2≠0.3）。改用 `Decimal(10, 2)` / `Numeric(10,2)`，Python 侧注解写 `Mapped[Decimal]`。

> ORM 的本质：这个类就是 `book` 表的"一面镜子"。`Base.metadata` 里保存着这张表的完整蓝图（列、类型、约束、注释），`create_all` 就是把蓝图翻译成 DDL。

---

## 4. 建表函数（45–49 行）

```python
async def create_table():
    async with async_engine.begin() as conn:                 # ①
        await conn.run_sync(Base.metadata.create_all)        # ②
```

**① `async_engine.begin()`**
- 从连接池借一条连接，进入时自动 `BEGIN`（开启事务），`async with` 正常退出时 **COMMIT**，抛异常则 **ROLLBACK** 并归还连接。
- 返回值 `conn` 是 `AsyncConnection`（异步连接）。

**② 为什么必须 `run_sync(Base.metadata.create_all)`？（核心难点）**
- `MetaData.create_all()` 是**同步 API**：DDL 编译、执行、反射都要做阻塞 I/O，SQLAlchemy 并没有提供 async 版本。
- 如果直接 `await` 它——它根本不是协程，`await` 会报错；如果直接同步调用——会**阻塞整个事件循环**，导致所有并发请求卡死（异步编程第一大忌）。
- `run_sync(fn)` 的做法是：把 `fn` 丢到**线程池**执行（`loop.run_in_executor`），并用 greenlet 桥接，使其内部对同步 DBAPI 的调用不会阻塞事件循环；调用结束后把结果 await 回来。
- 注意传入的是**函数对象** `Base.metadata.create_all`（**不带括号**），`run_sync` 会把同步版 `Connection` 作为第一个参数注入，等价于执行 `Base.metadata.create_all(sync_conn)`。需要额外参数可写 `conn.run_sync(Base.metadata.create_all, checkfirst=True)`。

**`create_all` 的行为边界（重要）**
- 默认 `checkfirst=True`：表已存在就跳过，**只建不存在的表**。
- **不会做结构迁移**：你给模型加一个字段、改字段类型，`create_all` 什么都不做。表结构演进请用 **Alembic**（`alembic revision --autogenerate`）。
- 启动时自动建表适合 demo/教学；**生产环境建议交给迁移工具**，因为多 worker 并发启动会有竞态，且无法追踪变更历史。

---

## 5. lifespan 生命周期（51–62 行）

```python
# @app.on_event("startup")     ← 已弃用写法（注释掉的部分）
# async def startup_event():
#     await create_table()

@asynccontextmanager                     # ①
async def lifespan(app: FastAPI):        # ②
    await create_table()                 # ③ yield 之前 = startup
    yield                                # ④ 交接控制权
    await async_engine.dispose()         # ⑤ yield 之后 = shutdown

app = FastAPI(lifespan=lifespan)         # ⑥
```

**① `@asynccontextmanager`**
把"只有一个 `yield` 的异步生成器"变成异步上下文管理器：进入上下文 = 执行到 yield 前；退出上下文 = 执行 yield 之后的代码。这是 `yield` 能表达"启动/关闭"这对动作的根本原因。

**② 签名固定**：`async def lifespan(app: FastAPI)` —— ASGI 规范（Lifespan Protocol）定义的接口，Starlette 会把 app 实例传进来。

**③ startup 段**：在这里做**一次性资源初始化**：建表、预热缓存、加载模型、创建后台任务等。本例只建表。

**④ yield**：交出控制权，应用开始正常处理请求。yield 出的值（若 `yield {...}`）会成为 `request.state` 的初始状态——这是 `on_event` 做不到的。

**⑤ shutdown 段**：`yield` 之后的代码在应用关闭时执行。
- `async_engine.dispose()`：把连接池里所有连接**优雅关闭**（发送 quit、关闭 socket），并把池标记为 disposed。若之后还有请求，会新建一个池。
- 不做这一步，进程退出时可能留下未正常关闭的 TCP 连接，MySQL 端要等 `wait_timeout` 才回收；在测试/热重载场景下还会出现连接泄漏。

**⑥ 必须在构造时传入**：`FastAPI(lifespan=lifespan)`。lifespan 函数要先定义、app 后创建，本例顺序正确。

### 相比 `@app.on_event` 的优势

| 维度 | `@app.on_event("startup"/"shutdown")` | `lifespan` |
|---|---|---|
| 标准性 | FastAPI 私有扩展 | **ASGI 官方 Lifespan 协议**，任何 ASGI 服务器（uvicorn/hypercorn）都一致支持 |
| 现状 | FastAPI 0.109 起**已弃用**，触发 `DeprecationWarning` | 官方推荐的唯一方式 |
| 资源配对 | startup 与 shutdown 是**两个独立函数**，靠全局变量传状态，容易漏写清理 | 一个函数里 `yield` 前后天然配对，`try/finally` 语义清晰 |
| 状态传递 | 需挂 `app.state` 全局变量 | 可直接 `yield {"key": value}` 注入 `request.state` |
| 测试 | `TestClient` 对 on_event 支持不一致 | `TestClient` 作为上下文管理器时能正确触发完整生命周期 |
| 异常处理 | 分散 | 一个 `try/except/finally` 覆盖全流程 |

### 本段可改进
- `create_table()` 若抛异常，`yield` 之后的代码**不会执行**（生成器被销毁时只会收到 `GeneratorExit`，而这里异常直接传播），`dispose()` 被跳过。稳妥写法：
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      try:
          await create_table()
          yield
      finally:
          await async_engine.dispose()
  ```

---

## 6. 会话工厂（64–70 行）

```python
AsyncSessionLocal = async_sessionmaker(   # ①
    bind=async_engine,                    # ②
    class_=AsyncSession,                  # ③
    expire_on_commit=False,               # ④
)
```

**① `async_sessionmaker`**
- SQLAlchemy 2.0 的**新名字**（旧的 `AsyncSessionMaker` 已弃用）。
- 它是个**工厂**：`AsyncSessionLocal()` 每次调用返回一个**新的** `AsyncSession`。不是单例——每个请求一个 session，这是正确姿势（session 非线程安全、非协程安全）。

**② `bind=async_engine`**：把这个工厂绑定到引擎上，之后 session 需要连接时向该引擎的池借。也可以在运行时 `async_sessionmaker(bind=...)` 或不绑定、用 `AsyncSession(bind=engine)`。

**③ `class_=AsyncSession`**：指定工厂产出的类。留空默认也是 `AsyncSession`；这里显式写出便于阅读，也方便将来换成自定义子类（比如加了审计字段自动填充的子类）。

**④ `expire_on_commit=False`（异步场景几乎必配）**
- 默认 `True`：一旦 `commit()`，session 会把所有已加载对象的属性标记为"过期"，下次访问 `book.price` 时**自动发一条 SELECT 去刷新**（这就是 lazy reload）。
- 在异步代码里，这次"自动刷新"发生在**同步上下文**中，无法 `await`，会抛：
  `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called`（或 `DetachedInstanceError`）。
- 设为 `False`：`commit()` 后对象属性**保持内存中已加载的值**，commit 之后还能直接读 `book.id` 返回给客户端，无需再查一次。
- 代价：数据可能不是库里的最新值（对"提交后立刻返回"的场景无影响）。

**另一个建议参数**：`autoflush=False`。默认 `True` 时，每次执行查询前会自动把待处理的变更 flush 到数据库，产生**隐式 I/O** 和意外写入；关掉后行为更可预测（需要时手动 `await session.flush()`）。

---

## 7. 依赖项 get_database（72–82 行）

```python
async def get_database():                          # ①
    async with AsyncSessionLocal() as session:     # ②
        try:
            yield session                          # ③
            await session.commit()                 # ④
        except Exception:                          # ⑤
            await session.rollback()
            raise                                  # ⑥
        finally:
            await session.close()                  # ⑦
```

**① 这是一个"带 yield 的依赖"**：FastAPI 看到依赖函数里有 `yield`，就把它当作**上下文管理器**处理——`yield` 产出的值注入路由参数，路由**返回响应之后**才回到这里执行收尾代码。

**② `async with AsyncSessionLocal() as session`**
- 创建 session。**注意此时并未真正借连接**——连接是惰性的，第一次 `execute()` 时才从池里借。
- `async with` 退出时会调用 `session.close()`：释放连接回池（或丢弃溢出连接），并清空 session 的 identity map。

**③ `yield session`**：把 session 交给路由函数使用。**执行流在这里暂停**，直到路由函数执行完毕、响应已发出，控制权才回到这一行后面。

**④ `await session.commit()`**（路由成功返回后执行）
- 提交事务：把 session 里累积的 INSERT/UPDATE/DELETE 真正落库，结束事务，释放行锁。
- **时机细节**：FastAPI 在**生成响应之后**才执行依赖的收尾代码。所以若 commit 失败，客户端已经收到 200 了，异常只能在服务端日志里看到——这是"依赖里 commit"的固有风险。真正需要强一致反馈的写操作，建议在路由函数里显式 `commit()` 并 try/except 返回错误响应。
- **本路由是纯查询**，其实没有东西可提交，这次 commit 是一次空操作（但仍会结束事务、把连接归还）。可以按操作类型区分：查询依赖不 commit，写操作依赖才 commit。

**⑤ `except Exception: rollback()`**
- 触发时机有两种：
  1. 路由函数内部抛异常 → FastAPI 把异常**抛回生成器**，在 `yield` 处抛出 → 被这里捕获；
  2. `await session.commit()` 本身失败（唯一约束冲突、连接断开、死锁等）。
- `rollback()` 撤销事务内所有未提交变更，保证"要么全成功，要么全不改"（原子性）。

**⑥ `raise`（绝不能省）**
- 把异常**原样继续抛出**，交给 FastAPI 的异常处理链（500 或你注册的 exception handler）。
- 如果吞掉异常，客户端会收到 **200 空响应**，错误被静默埋掉——这是初学者最常踩的坑。

**⑦ `finally: await session.close()`**
- 无论成功、失败、还是客户端中途断开（CancelledError 属于 BaseException，其实这里捕获不到），都会关闭 session。
- **冗余说明**：`async with AsyncSessionLocal() as session` 退出时本身就会 `close()`，因此这行是**重复调用**。好在 `Session.close()` 是**幂等**的（对已关闭的 session 再 close 无副作用），所以不会出错，只是多写一行。保留它能强化"连接一定要归还"的意识，但也可以精简掉。
- 想覆盖取消场景，可用 `except BaseException`（覆盖 `asyncio.CancelledError`）或把 `finally` 保留在 `async with` 外层。

### 执行时序一览

```
请求进入
  └─ FastAPI 创建依赖 → 进入 get_database()
       └─ 创建 session（未连库）
       └─ yield session ──────────────┐
                                      ▼
                              路由 get_book_list()
                              await db.execute(...)   ← 此刻才借连接
                              返回 ORM 对象列表
                                      │
       ←──────────────────────────────┘
       └─ await session.commit()      （无异常时）
       └─ except: rollback() + raise  （有异常时）
       └─ finally: close()            （兜底）
  └─ 退出 async with → close()（幂等）
响应发出
```

---

## 8. 查询路由（84–89 行）

```python
@app.get("/book/books")                                       # ①
async def get_book_list(db: AsyncSession = Depends(get_database)):  # ②
    result = await db.execute(select(Book))                   # ③
    book = result.scalars().all()                             # ④
    return book                                               # ⑤
```

**① 路由装饰器**：注册 GET `/book/books`。命名上可简化为 `/books`（RESTful 惯例里 `/book/books` 有冗余）。

**② 依赖注入**：`Depends(get_database)` 告诉 FastAPI："调用这个路由前先执行 `get_database`，把它 yield 的值赋给参数 `db`"。注解 `AsyncSession` 让 IDE 获得类型提示。

**③ `await db.execute(select(Book))`（完整流程，重点）**

1. **构造语句**：`select(Book)` 生成一个 `Select` 对象。它不是字符串，而是可组合的**表达式树**。最终编译成：
   ```sql
   SELECT book.id, book.bookname, book.author, book.price,
          book.publisher, book.create_time, book.update_time
   FROM book
   ```
   （SQLAlchemy 2.0 风格，取代 1.x 的 `session.query(Book)`。可继续链式写 `.where(...)`、`.order_by(...)`、`.limit(...)`、`.options(selectinload(...))`。）

2. **获取连接**：session 第一次执行语句时向引擎的**连接池**借一条连接（受 `pool_size/max_overflow` 约束），必要时开启事务。

3. **编译 SQL**：按 MySQL 方言把表达式树编译成带占位符的 SQL 字符串（方言决定引号风格、参数风格、类型名）。

4. **发送并等待**：通过 aiomysql 发送 SQL，**`await` 挂起协程，事件循环转去服务其他请求**——这正是异步的价值：等待数据库 I/O 时不占用线程。

5. **接收结果**：驱动读回结果集。

6. **ORM 构造对象**：把每行数据填充成 `Book` 实例，并登记到 session 的 **identity map**（同一主键返回同一个 Python 对象，保证对象唯一性与变更追踪）。
   - 因为 `await` 已经完成了 I/O，`db.execute()` 返回的是**同步的 `Result`**，所以后面 `.scalars().all()` 不需要 `await`。
   - 若用 `db.stream()` 流式读取，则返回 `AsyncResult`，需要 `async for`。

**④ `result.scalars().all()`**
- `result`（`Result`）默认迭代出 `Row` 对象（类似具名元组，可按索引/属性取多列）。
- `.scalars()`：把每行**只取第一列**，得到 `ScalarResult`。因为 `select(Book)` 只选了实体本身，第一列就是 `Book` 对象——于是得到一串 `Book`。
- `.all()`：把结果**全部取回内存**，返回 `list[Book]`。
- 更简洁的 2.0 写法：`books = (await db.scalars(select(Book))).all()`。
- 数据量大时应分页：`.limit(n).offset(m)`，避免一次性把整表拉进内存。

**⑤ `return book`（这里有坑，必须改）**
- FastAPI 用 `jsonable_encoder` 做序列化，它对 **SQLAlchemy ORM 实例没有原生支持**：会退化为尝试 `dict(obj)` → 失败 → 尝试 `vars(obj)` → 把 `_sa_instance_state` 等**内部对象**也塞进结果，最终要么报错（"Unable to serialize unknown type"），要么输出脏数据，且字段完全不可控（可能暴露不该暴露的列）。
- **正确做法**：定义 Pydantic schema 并声明 `response_model`：
  ```python
  from pydantic import BaseModel, ConfigDict

  class BookSchema(BaseModel):
      model_config = ConfigDict(from_attributes=True)   # 允许从 ORM 对象读取（v2 必配）
      id: int
      bookname: str
      author: str
      price: float
      publisher: str

  @app.get("/books", response_model=list[BookSchema])
  async def get_book_list(db: AsyncSession = Depends(get_database)):
      return (await db.scalars(select(Book))).all()
  ```
  好处：输出字段可控、自动 OpenAPI 文档、自动校验。

---

## 9. 值得注意 / 可改进清单（速查）

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| 1 | URL | 账号密码硬编码在源码里 | 用 `pydantic-settings` + `.env` 读取，禁止入库 |
| 2 | 引擎 | 缺 `pool_pre_ping` / `pool_recycle` | 加 `pool_pre_ping=True, pool_recycle=3600`，防 MySQL 8 小时断连 |
| 3 | 引擎 | `echo=True` 常开 | 生产关闭，用环境变量控制 |
| 4 | 模型 | `price` 用 `Float` | 改 `Decimal(10, 2)`（`Mapped[Decimal]`），金额不用浮点 |
| 5 | 模型 | 只有 `insert_default`，无 `server_default` | 非 ORM 途径插入时时间会为 NULL；需要表级兜底请加 `server_default=func.now()` |
| 6 | 模型 | 时间无时区约定 | 统一 UTC；必要时 `DateTime(timezone=True)` |
| 7 | 建表 | 启动时 `create_all` | demo 可以，生产用 **Alembic** 迁移 |
| 8 | lifespan | `create_table()` 失败则跳过 `dispose()` | 用 `try/finally` 包裹 |
| 9 | 依赖 | `commit()` 后才发响应失败反馈 | 写操作在路由内显式 commit + 错误处理；查询依赖可不 commit |
| 10 | 依赖 | `finally` 里的 `close()` 与 `async with` 重复 | 幂等无害，可精简；另有 `CancelledError` 覆盖不到的问题 |
| 11 | 路由 | 直接返回 ORM 对象 | 用 `response_model` + Pydantic schema（`from_attributes=True`） |
| 12 | 路由 | 无分页 | 大数据量加 `limit/offset`，或 `fastapi-pagination` |
| 13 | 路由 | 路径 `/book/books` 冗余 | 改 `/books` |
| 14 | 依赖 | 无类型化依赖声明 | 用 `Annotated[AsyncSession, Depends(get_database)]`，可复用 |
| 15 | 环境 | aiomysql + MySQL 8 | MySQL 8 默认 `caching_sha2_password`，需 `pip install cryptography` |

### 推荐的最小改动版（片段）

```python
from decimal import Decimal
from typing import Annotated
from pydantic import BaseModel, ConfigDict

async_engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

DbDep = Annotated[AsyncSession, Depends(get_db)]

class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    bookname: str
    author: str
    price: Decimal
    publisher: str

@app.get("/books", response_model=list[BookOut])
async def get_book_list(db: DbDep, limit: int = 20, offset: int = 0):
    stmt = select(Book).order_by(Book.id).limit(limit).offset(offset)
    return (await db.scalars(stmt)).all()
```

---

## 10. 一句话总结每个块的"设计意图"

| 代码块 | 设计意图 |
|---|---|
| 导入 | 引入异步适配层与 2.0 声明式建模工具 |
| `create_async_engine` | 持有连接池 + 方言，是所有数据库 I/O 的入口；惰性建连 |
| `Base` / `Book` | 用类型注解声明 schema，一份代码同时服务 Python 类型检查与 DDL 生成 |
| `create_table` + `run_sync` | 同步 DDL 与异步事件循环的桥；只在启动时做一次 |
| `lifespan` | 用 `yield` 把"初始化/清理"配成一对，符合 ASGI 标准 |
| `async_sessionmaker` | 每次请求一个 session 的工厂；`expire_on_commit=False` 规避异步隐式 I/O |
| `get_database` | 请求级事务边界：成功提交、失败回滚、一定关闭 |
| `/book/books` | 2.0 风格 `select` → `execute` → `scalars().all()` 的查询链路 |

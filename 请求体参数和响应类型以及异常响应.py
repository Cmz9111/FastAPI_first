#请求体参数和响应类型以及异常响应


from fastapi import FastAPI, HTTPException
from pydantic import BaseModel,Field
from fastapi.responses import HTMLResponse,FileResponse

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello World"}


#注册 : 用户名和密码 -> str
class User(BaseModel):
    username: str = Field(default="张三",min_length=3,max_length=10,description="用户名长度需在3-10之间")
    password: str = Field(min_length=3,max_length=20)

@app.post("/register")
async def register(user: User):
    return user

# class Book(BaseModel):
#     book_name: str
#     author: str
#     publisher: str
#     sale_price: float
#
# @app.post("/addBook")
# async def add_book(book: Book):
#     return book

#接口 -> 响应HTML代码    装饰器中指定响应类
@app.get("/html", response_class=HTMLResponse)
async def get_html():
    return "<h1>hello world</h1>"


#接口 返回一张图片内容    返回响应对象
@app.get("/file")
async def get_file():
    path = "./files/1.jpeg"
    return FileResponse(path)

# #需求: 新闻接口 -> 响应数据格式 id、title、content
# class News(BaseModel):
#     id: int
#     title: str
#     content: str
#
# @app.get("/news/{id}",response_model=News)
# async def get_news(id: int):
#     return {
#         "id": id,
#         "title": f"这是第{id}本书",
#         "content": "这是一本好书"
#     }

#需求: 按id 查询新闻 1-6
@app.get("/news/{id}")
async def get_news(id: int):
    id_list = [1,2,3,4,5,6]
    if id not in id_list:
        raise HTTPException(status_code=404, detail="您查找的新闻不存在")
    return {"id": id}
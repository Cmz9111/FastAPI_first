#路径参数和查询参数

from fastapi import FastAPI,Path,Query

# 创建FastAPI实例
app = FastAPI()


@app.get("/")
async def root():
    return {"message": "Hello World"}

#路径参数
@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}

@app.get("/hello")
async def hello_fastapi():
    return{"msg": "Hello FastAPI"}

@app.get("/hello/{id}/{name}")
#default:...表示这个 id 路径参数没有默认值，是必填项
async def hello_fastapi_id(id: int = Path(...,gt=0,lt=101,description="人物编号，取值1-100"),
                           name: str = Path(...,min_length=1,max_length=10,description="人物名称长度应在1-10之间")):
    return {"msg": f"Hello id:{id} name:{name}"}

#查询参数
#需求 查询新闻 ->分页,skip: 跳过的记录数,limit:返回的记录数 10
@app.get("/news/news_list")
#路径参数用Path做参数注解，查询参数用Query做参数注解，
#只是用的包不同，作用都是限定参数的类型大小，进行一些说明
async def get_news_list(
        skip: int = Query(0,description="跳过的记录数",lt=10),
        limit: int = Query(default=10,description="返回的记录数")):
    return {"skip": skip, "limit": limit}

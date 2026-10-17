#依赖注入


from fastapi import Depends,Query,FastAPI #2.导入Depends

app = FastAPI()

#1.创建依赖项
async def common_parameters(
    skip: int = Query(0,ge = 0),
    limit: int = Query(0,le = 20)
):
    return {"skip": skip, "limit": limit}

#3.声明依赖项 -> 依赖注入
@app.get("news/news_list")
async def get_news_list(
        commons = Depends(common_parameters)
):
    return commons
#中间件

from fastapi import FastAPI

app = FastAPI()


@app.middleware("http")
async def middleware1(request,call_next):
    print("中间件1开始处理")
    response = await call_next(request)
    print("中间件1结束处理")
    return response

@app.middleware("http")
async def middleware2(request,call_next):
    print("中间件2开始处理")
    response = await call_next(request)
    print("中间件2结束处理")
    return response
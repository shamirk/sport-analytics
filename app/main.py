from fastapi import FastAPI

app = FastAPI(title="USPSA Analytics", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok"}

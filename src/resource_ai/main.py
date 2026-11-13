from fastapi import FastAPI

from resource_ai.api.routes.analysis import router as analysis_router
from resource_ai.api.routes.graph import router as graph_router
from resource_ai.api.routes.intake import router as intake_router
from resource_ai.api.routes.rag import router as rag_router

app = FastAPI(
    title="Re:Source AI",
    version="0.1.0",
    description="폐기물 처리 의사결정을 위한 AI·지식 기반 서비스",
)
app.include_router(rag_router)
app.include_router(graph_router)
app.include_router(analysis_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(intake_router)

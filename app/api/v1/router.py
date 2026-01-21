from fastapi import APIRouter

from app.api.v1 import auth, binding, study, upload, correction, question, conversation, solving

api_router = APIRouter()

# Include all routers
api_router.include_router(auth.router, prefix="/auth", tags=["认证模块"])
api_router.include_router(binding.router, prefix="/binding", tags=["绑定模块"])
api_router.include_router(binding.router, prefix="/bindding", tags=["绑定模块"])
api_router.include_router(study.router, prefix="/study", tags=["学习记录模块"])
api_router.include_router(upload.router, prefix="/upload", tags=["文件上传模块"])
api_router.include_router(correction.router, prefix="/correction", tags=["作业批改模块"])
api_router.include_router(solving.router, prefix="/solving", tags=["拍照答疑模块"])
api_router.include_router(question.router, prefix="/question", tags=["题目详情模块"])
api_router.include_router(conversation.router, prefix="/conversation", tags=["AI对话模块"])

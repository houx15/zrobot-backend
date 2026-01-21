# AI智慧学习平板 - 后端开发计划

**创建日期**: 2026年1月20日
**状态**: 进行中

---

## 开发阶段总览

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | 后端基础架构 | ✅ 已完成 |
| Phase 2 | 简单API实现 | ✅ 已完成 |
| Phase 3 | 业务API实现 | ✅ 已完成 |
| Phase 4 | WebSocket实现 | ✅ 已完成 |
| Phase 5 | AI Agent实现 | ✅ 已完成 |

---

## Phase 1: 后端基础架构 ✅

**目标**: 搭建FastAPI项目骨架，配置数据库和缓存连接

### 完成内容
- [x] 项目目录结构创建
- [x] 配置管理 (pydantic-settings, .env)
- [x] 数据库模型 (SQLAlchemy 2.0 async)
  - StudentUser, ParentUser
  - ParentStudentBinding
  - HomeworkCorrectionHistory, QuestionHistory
  - AIConversationHistory
  - StudyRecord, KnowledgePointRecord
- [x] Alembic迁移配置 (async支持)
- [x] Redis客户端封装
- [x] 基础响应模型和错误码
- [x] JWT认证工具
- [x] FastAPI应用骨架和路由结构

---

## Phase 2: 简单API实现 ✅

**目标**: 实现不依赖外部API的基础接口

### 完成内容
- [x] **认证模块完善**
  - [x] 登录接口完善 (完整的logout实现)
  - [x] Token黑名单机制 (Redis)
  - [x] 创建测试用户脚本 (scripts/create_test_user.py)

- [x] **绑定模块完善**
  - [x] 二维码生成服务 (services/qrcode.py)
  - [x] 二维码上传到OSS或返回base64
  - [x] 绑定状态轮询

- [x] **学习记录模块**
  - [x] 开始学习接口 (POST /study/start)
  - [x] 结束学习接口 (POST /study/end)
  - [x] 学习记录查询接口 (GET /study/history)
  - [x] 简单记录接口 (POST /study/record)

- [x] **文件上传模块**
  - [x] 阿里云OSS STS服务 (services/oss.py)
  - [x] 支持image/audio/video类型
  - [x] 文件路径规范化

---

## Phase 3: 业务API实现 ✅

**目标**: 集成智谱AI，实现核心业务功能

### 完成内容
- [x] **智谱API服务封装** (services/zhipu.py)
  - [x] 作业批改API集成 (homework correction)
  - [x] 拍照解题API集成 (problem solving)
  - [x] 流式响应支持

- [x] **作业批改模块** (api/v1/correction.py)
  - [x] 提交批改接口完整实现
  - [x] 解析智谱API响应
  - [x] 创建QuestionHistory记录
  - [x] 自动记录学习时长
  - [x] 知识点提取和记录
  - [x] 批改历史查询

- [x] **拍照答疑模块** (api/v1/solving.py)
  - [x] 提交解题接口
  - [x] 流式解题响应
  - [x] 答疑历史查询

- [x] **题目详情模块** (api/v1/question.py)
  - [x] 题目详情查询
  - [x] 流式获取题目解析
  - [x] 错题本功能 (添加/移除)

---

## Phase 4: WebSocket实现 ✅

**目标**: 实现AI实时对话的WebSocket通信

### 完成内容
- [x] **WebSocket基础设施** (websocket/manager.py)
  - [x] WebSocket路由和连接管理
  - [x] Token验证 (utils/security.py - decode_ws_token)
  - [x] 心跳机制 (ping/pong)
  - [x] 连接状态管理

- [x] **消息协议实现** (websocket/protocol.py)
  - [x] 客户端→服务端: audio, text, image, interrupt, ping
  - [x] 服务端→客户端: audio, transcript, reply_text, reply_start, reply_end, error, pong

- [x] **Redis会话管理**
  - [x] conv:session:{id} - 会话状态
  - [x] conv:messages:{id} - 消息列表
  - [x] conv:vars:{id} - 上下文变量
  - [x] conv:interrupt:{id} - 打断控制
  - [x] 会话超时处理

- [x] **对话HTTP接口完善** (api/v1/conversation.py)
  - [x] 创建会话 (完整Redis初始化)
  - [x] 结束会话 (数据持久化到MySQL)
  - [x] 历史记录和详情

- [x] **WebSocket Handler** (websocket/handler.py)
  - [x] 消息路由和处理
  - [x] 打断机制框架
  - [x] 占位符响应 (待Phase 5集成ASR/TTS/LLM)

---

## Phase 5: AI Agent实现 ✅

**目标**: 集成ASR/TTS/LLM，实现完整的语音对话能力

### 完成内容
- [x] **豆包ASR集成** (services/asr.py)
  - [x] 流式语音识别服务
  - [x] PCM音频处理 (16kHz, 16-bit, mono)
  - [x] 中间结果和最终结果处理
  - [x] WebSocket协议实现

- [x] **豆包TTS集成** (services/tts.py)
  - [x] 流式语音合成服务
  - [x] MP3音频输出
  - [x] 分段合成支持

- [x] **豆包LLM集成** (services/llm.py)
  - [x] 流式文本生成
  - [x] OpenAI兼容API接口
  - [x] 多轮对话管理

- [x] **Prompt模板设计** (services/prompts.py)
  - [x] 答疑场景Prompt (solving)
  - [x] 闲聊场景Prompt (chat)
  - [x] 上下文变量注入 (学生姓名、年级、题目等)
  - [x] 引导式教学风格

- [x] **AI Agent编排** (services/agent.py)
  - [x] ASR → LLM → TTS完整流程
  - [x] 会话上下文管理
  - [x] 消息历史处理

- [x] **自然打断机制**
  - [x] 打断信号处理
  - [x] 中断TTS播放
  - [x] 取消LLM生成
  - [x] 音频缓冲区清理

- [x] **WebSocket Handler更新** (websocket/handler.py)
  - [x] 文本消息→LLM→TTS流程
  - [x] 音频消息→ASR→LLM→TTS流程
  - [x] 错误处理和恢复

---

## 技术栈

| 组件 | 技术选型 |
|------|---------|
| Web框架 | FastAPI |
| 数据库 | PostgreSQL + asyncpg |
| ORM | SQLAlchemy 2.0 (async) |
| 缓存 | Redis |
| 迁移 | Alembic |
| 认证 | JWT (python-jose) |
| 对象存储 | 阿里云OSS |
| 作业批改 | 智谱AI |
| 语音识别 | 豆包ASR |
| 语音合成 | 豆包TTS |
| 大模型 | 豆包LLM |

---

## 文件结构

```
backend/
├── app/
│   ├── main.py              # FastAPI入口
│   ├── config.py            # 配置管理
│   ├── database.py          # 数据库连接
│   ├── redis_client.py      # Redis客户端
│   ├── models/              # SQLAlchemy模型
│   ├── schemas/             # Pydantic模式
│   ├── api/v1/              # API路由
│   ├── services/            # 业务逻辑
│   │   ├── auth.py
│   │   ├── oss.py           # OSS服务
│   │   ├── zhipu.py         # 智谱AI服务
│   │   ├── asr.py           # ASR服务
│   │   ├── tts.py           # TTS服务
│   │   └── llm.py           # LLM服务
│   ├── utils/               # 工具函数
│   └── websocket/           # WebSocket处理
│       ├── handler.py       # 连接处理
│       ├── protocol.py      # 消息协议
│       └── manager.py       # 连接管理
├── alembic/                 # 数据库迁移
├── tests/                   # 测试用例
├── requirements.txt
├── .env.example
└── PLAN.md                  # 本文档
```

---

## 进度更新日志

### 2026-01-20
- ✅ Phase 1 完成：后端基础架构搭建完毕
- ✅ Phase 2 完成：简单API实现
  - 认证模块（登录/登出/Token黑名单）
  - 绑定模块（二维码生成/状态查询）
  - 学习记录模块（开始/结束/历史查询）
  - 文件上传模块（OSS STS凭证）
- ✅ Phase 3 完成：业务API实现
  - 智谱API服务封装（作业批改+拍照解题）
  - 作业批改模块（完整批改流程）
  - 拍照答疑模块（支持流式响应）
  - 题目详情模块（解析+错题本）
- ✅ Phase 4 完成：WebSocket实现
  - WebSocket连接管理（websocket/manager.py）
  - 消息协议定义（websocket/protocol.py）
  - WebSocket处理器（websocket/handler.py）
  - 对话HTTP接口完善（api/v1/conversation.py）
  - Redis会话管理和打断控制
- ✅ Phase 5 完成：AI Agent实现
  - ASR服务（services/asr.py）- 豆包语音识别
  - TTS服务（services/tts.py）- 豆包语音合成
  - LLM服务（services/llm.py）- 豆包大模型
  - Prompt模板（services/prompts.py）- 答疑+闲聊场景
  - AI Agent编排（services/agent.py）- ASR→LLM→TTS流程
  - WebSocket Handler集成完整AI对话流程
  - 自然打断机制实现

### 2026-01-21
- ✅ 全部5个Phase开发完成！
- 后端API和WebSocket功能已就绪

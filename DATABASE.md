# AI智慧学习平板 - 数据库设计文档

**版本**：v1.1  
**日期**：2026年1月20日  
**数据库**：PostgreSQL 15+  
**后端框架**：Python FastAPI

---

## 数据类型说明（PostgreSQL）

| 类型 | 说明 |
|------|------|
| `bigint` | 64位整数，用于主键ID |
| `text` | 变长文本，无长度限制 |
| `varchar(n)` | 限定长度的字符串 |
| `boolean` | 布尔值 true/false |
| `timestamp` | 时间戳（不带时区） |
| `timestamptz` | 时间戳（带时区，推荐） |
| `jsonb` | 二进制JSON，**可索引、可查询** |
| `numeric(p,s)` | 精确数值 |
| `integer` | 32位整数 |
| `smallint` | 16位整数（替代tinyint） |

---

## 2. 表结构设计

### 2.1 student_user（学生用户）

| 字段 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| id | bigint | ✓ | 主键，自增 (SERIAL) |
| phone | varchar(20) | ✓ | 手机号，唯一 |
| nickname | varchar(50) | | 称呼/昵称 |
| personality | varchar(200) | | 性格描述（用于AI个性化） |
| grade | varchar(20) | | 年级，枚举值见下方 |
| study_profile | jsonb | | 学习画像，结构见下方 |
| device_id | varchar(100) | | 设备标识 |
| last_login_at | timestamptz | | 最后登录时间 |
| created_at | timestamptz | ✓ | 注册时间，默认 now() |
| updated_at | timestamptz | ✓ | 更新时间 |
| is_deleted | boolean | ✓ | 软删除标记，默认 false |

**study_profile 结构（JSONB）：**
```python
{
    "weak_subjects": ["math", "physics"],      # 薄弱学科
    "strong_subjects": ["english", "chinese"], # 优势学科
    "learning_style": "visual",                # 学习风格
    "avg_daily_duration": 45,                  # 日均学习分钟数
    "total_questions_solved": 128,             # 累计答题数
    "accuracy_rate": 0.75,                     # 总体正确率
    "last_updated": "2026-01-20T10:00:00Z"
}
```

**grade 枚举值：**
- `primary_1` ~ `primary_6`：小学一至六年级
- `junior_1` ~ `junior_3`：初一至初三
- `senior_1` ~ `senior_3`：高一至高三

---

### 2.2 parent_user（家长用户）

| 字段 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| id | bigint | ✓ | 主键 |
| phone | varchar(20) | | 手机号 |
| nickname | varchar(50) | | 昵称 |
| avatar_url | text | | 头像 |
| created_at | timestamptz | ✓ | 注册时间 |
| updated_at | timestamptz | ✓ | 更新时间 |
| is_deleted | boolean | ✓ | 软删除 |

---

### 2.3 parent_student_bindding（家长-学生绑定）

| 字段 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| id | bigint | ✓ | 主键 |
| parent_id | bigint | ✓ | 家长ID |
| student_id | bigint | ✓ | 学生ID |
| relation | varchar(20) | | 关系：father / mother / grandpa / grandma / other |
| bindded_at | timestamptz | ✓ | 绑定时间 |
| unbindded_at | timestamptz | | 解绑时间 |
| status | smallint | ✓ | 1=绑定中，0=已解绑 |
| created_at | timestamptz | ✓ | 创建时间 |

**约束**：(parent_id, student_id, status) 联合唯一，防止重复绑定

---

### 2.4 homework_correction_history（批改历史-主表）

| 字段 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| id | bigint | ✓ | 主键 |
| user_id | bigint | ✓ | 学生ID |
| image_url | text | ✓ | 原始作业图片URL |
| subject | varchar(20) | | 学科（API返回的 paper_subject） |
| processed_image_url | text | | 批改后标注图片URL |
| total_questions | integer | | 总题数（API返回的 stat_result.all） |
| correct_count | integer | | 正确数（stat_result.right） |
| wrong_count | integer | | 错误数（stat_result.wrong） |
| correcting_count | integer | | 批改中数量（stat_result.correcting） 
| raw_response | jsonb | | **完整API响应JSON**（备份用，用JSONB可查询） |
| api_trace_id | varchar(100) | | 智谱API trace_id |
| status | smallint | ✓ | 0=处理中，1=完成，2=失败 |
| created_at | timestamptz | ✓ | 创建时间 |
| is_deleted | boolean | ✓ | 软删除 |

---

### 2.6 question_history 题目历史-答疑/批改

每道题拆分存储，方便统计和关联错题本。

| 字段 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| id | bigint | ✓ | 主键 |
| correction_id | bigint |  | 关联 homework_correction_history.id |
| conversation_id | bigint |  | 关联AI对话历史|
| user_id | bigint | ✓ | 学生ID（冗余，方便查询） |
| source | varchar | ✓ | 来源：solving=答疑 / correction=批改|
| subject | varchar | | 学科 |
| question_index | integer | ✓ | 题目序号（API返回的 index） |
| question_uuid | varchar(100) | | API返回的 uuid |
| question_text | text | | 题目文本（API返回的 text 或 question） |
| question_image_url | string(500) | | 题目图片|
| question_type | smallint | | 题目类型（API返回的 type） |
| user_answer | varchar(500) | | 用户答案 |
| correct_answer | varchar(500) | | 正确答案 |
| is_correct | boolean | | 是否正确（correct_result == 1） |
| analysis | text | | 解析文本 |
| knowledge_points | jsonb | | 从解析中提取的知识点 |
| question_bbox | jsonb | | 题目位置坐标（用于图片标注） |
| answer_bbox | jsonb | | 答案位置坐标（用于图片标注） |
| correct_source | smallint | | 批改来源（1=题库，3=模型等） |
| api_trace_id | string(100) | | 智谱 API trace_id |
| is_finish | boolean | | 是否批改完成 |
| created_at | timestamptz | ✓ | 创建时间 |

**写入逻辑**：
1. 批改后：API返回后，遍历 `image_results[].results[]`，每道题插入一条记录。

2. 拍照答疑：调用智谱API返回后，存储一道新纪录。

**bbox 结构**（8个数字，4个点的坐标）：
```python
[x1, y1, x2, y2, x3, y3, x4, y4]  # 左上、右上、右下、左下
```

---

### 2.7 ai_conversation_history（AI对话历史）

| 字段 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| id | bigint | ✓ | 主键 |
| user_id | bigint | ✓ | 学生ID |
| type | varchar(20) | ✓ | 类型：solving=答疑 / chat=闲聊对话 |
| topic | varchar(200) | | 对话主题（AI自动总结或用户输入） |
| content | jsonb | | 对话内容JSON，结构见下方 |
| message_count | integer | | 消息条数 |
| total_duration | integer | | 对话时长（秒） |
| session_id | varchar(100) | | WebSocket会话ID |
| started_at | timestamptz | ✓ | 开始时间 |
| ended_at | timestamptz | | 结束时间 |
| created_at | timestamptz | ✓ | 创建时间 |
| is_deleted | boolean | ✓ | 软删除 |

**content 结构（JSONB）：**
```python
{
    "messages": [
        {
            "role": "user",           # user / assistant
            "type": "text",           # text / image / audio
            "content": "老师，这道题怎么做？",
            "timestamp": "2026-01-20T10:00:00Z"
        },
        {
            "role": "assistant",
            "type": "text",
            "content": "好的，我来帮你分析一下...",
            "timestamp": "2026-01-20T10:00:02Z"
        },
        {
            "role": "user",
            "type": "image",
            "content": "https://xxx/question.jpg",  # 图片URL
            "timestamp": "2026-01-20T10:00:05Z"
        }
    ]
}
```

---


### 2.9 study_record（学习记录）

| 字段 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| id | bigint | ✓ | 主键 |
| user_id | bigint | ✓ | 学生ID |
| action | varchar(30) | ✓ | 行为类型，枚举见下方 |
| duration | integer | | 时长（秒），后端计算 = end_time - start_time |
| abstract | varchar(500) | | 行为摘要，如"完成数学作业批改，正确率80%" |
| related_id | bigint | | 关联业务ID |
| related_type | varchar(30) | | 关联类型：correction / solving / conversation |
| status | smallint | ✓ | 0=进行中，1=已完成，2=异常结束 |
| created_at | timestamptz | ✓ | 创建时间 |

**action 枚举值：**
| 值 | 说明 |
|---|------|
| correction | 作业批改 |
| tutoring | 题目答疑 |
| chat | AI老师对话 |
| homework | 写作业 |

**计时流程：**
```
1. 前端进入页面 → 调用 POST /api/study/start
   → 后端：插入记录，start_time = now()，status = 0
   → 返回 record_id

2. 前端点击"完成"或"返回" → 调用 POST /api/study/end
   → 后端：end_time = now()，duration = end_time - start_time，status = 1


```

---

### 2.10 knowledge_point_record（知识点记录）

| 字段 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| id | bigint | ✓ | 主键 |
| user_id | bigint | ✓ | 学生ID |
| topic_name | varchar(100) | ✓ | 知识点名称 |
| subject | varchar(20) | | 所属学科 |
| question_count | integer | ✓ | 相关题目总数，默认0 |
| created_at | timestamptz | ✓ | 创建时间 |
| updated_at | timestamptz | ✓ | 更新时间 |

**约束**：(user_id, topic_name) 联合唯一


---


## 3. 数据字典

### 3.1 学科枚举（subject）

| 值 | 说明 |
|---|------|
| chinese | 语文 |
| math | 数学 |
| english | 英语 |
| physics | 物理 |
| chemistry | 化学 |
| biology | 生物 |
| history | 历史 |
| geography | 地理 |
| politics | 政治 |

### 3.2 学习行为枚举（action）

| 值 | 说明 |
|---|------|
| correction | 作业批改 |
| tutoring | 题目答疑 |
| chat | AI老师对话 |
| homework | 写作业 |


### 3.4 绑定关系枚举（relation）

| 值 | 说明 |
|---|------|
| father | 爸爸 |
| mother | 妈妈 |
| grandpa | 爷爷/外公 |
| grandma | 奶奶/外婆 |
| other | 其他 |

---

## 4. 关键业务流程的数据写入

### 4.1 作业批改流程

```
1. 用户上传图片
   → 存储图片到OSS，获取 image_url

2. 调用智谱API，获取响应

3. 写入 homework_correction_history（主表）
   → user_id, image_url, subject, processed_image_url
   → total_questions, correct_count, wrong_count
   → raw_response = 完整API响应JSON
   → api_trace_id = response.choices[0].messages[0].content.object.trace_id

4. 遍历 response.image_results[].results[]，写入 correction_question_detail
   → 每道题一条记录
   → is_correct = (item.correct_result == 1)

5. 筛选 is_correct=False 的题目，写入 wrong_question_history
   → source = "correction"
   → correction_history_id = 主表ID
   → correction_detail_id = 明细ID

6. 提取知识点，更新 knowledge_point_record
   → 若知识点不存在则创建
   → question_count += 1
   → correct_count 或 wrong_count += 1
   → 重新计算 mastery_level
```

### 4.2 题目答疑流程

```
1. 用户上传图片/输入文字

2. 调用智谱API，流式获取解答

3. 写入 question_solving_history
   → image_url / question_text
   → answer, analysis_text
   → knowledge_points = 从解析中提取

4. 若用户点击"加入错题本"
   → 写入 wrong_question_history
   → source = "solving"
   → solving_history_id = 答疑记录ID

5. 提取知识点，创建关联
   → 写入 knowledge_point_question_relation
   → 更新 knowledge_point_record
```

### 4.3 学习时长记录流程

```
进入页面时：
POST /api/study/start
{
    "user_id": 123,
    "action": "homework_correction"
}
→ 后端插入记录，返回 { "record_id": 456 }

离开页面时：
POST /api/study/end
{
    "record_id": 456,
    "related_id": 789,        # 批改记录ID（可选）
    "related_type": "correction",
    "abstract": "完成数学作业批改，正确率80%"
}
→ 后端计算 duration，更新 status=1
```

---

## 5. 索引建议

| 表 | 索引字段 | 说明 |
|---|---------|------|
| student_user | phone (唯一) | 登录查询 |
| parent_student_bindding | parent_id | 家长查绑定的学生 |
| parent_student_bindding | student_id | 学生查绑定的家长 |
| question_solving_history | (user_id, created_at) | 用户答疑历史列表 |
| question_solving_history | (user_id, subject) | 按学科筛选 |
| homework_correction_history | (user_id, created_at) | 用户批改历史列表 |
| correction_question_detail | correction_id | 查询某次批改的所有题目 |
| correction_question_detail | (user_id, is_correct) | 统计用户正确/错误题目 |
| wrong_question_history | (user_id, subject) | 按学科查错题 |
| wrong_question_history | (user_id, mastery_status) | 按掌握状态筛选 |
| study_record | (user_id, start_time) | 用户学习记录 |
| study_record | (user_id, action) | 按行为类型统计 |
| knowledge_point_record | (user_id, topic_name) 唯一 | 知识点唯一约束 |
| knowledge_point_record | (user_id, mastery_level) | 查薄弱知识点 |

---

## 6. PostgreSQL 特性说明

### 6.1 JSONB 的优势

PostgreSQL 的 `jsonb` 类型比 MySQL 的 `JSON` 更强大：

**可索引**
```sql
-- 为 JSONB 字段创建 GIN 索引
CREATE INDEX idx_study_profile ON student_user USING GIN (study_profile);

-- 查询 JSONB 内部字段
SELECT * FROM student_user 
WHERE study_profile @> '{"weak_subjects": ["math"]}';

-- 查询数组包含某个值
SELECT * FROM question_solving_history
WHERE knowledge_points ? '二次函数';
```

**可查询**
```sql
-- 提取 JSONB 字段
SELECT 
    id,
    study_profile->>'learning_style' AS learning_style,
    (study_profile->>'accuracy_rate')::float AS accuracy
FROM student_user;

-- 查询嵌套字段
SELECT * FROM ai_conversation_history
WHERE content->'messages'->0->>'role' = 'user';
```

### 6.2 时区处理

建议使用 `timestamptz`（带时区的时间戳）：

```sql
-- 存储时自动转换为 UTC
INSERT INTO student_user (created_at) VALUES (NOW());

-- 查询时根据会话时区显示
SET timezone = 'Asia/Shanghai';
SELECT created_at FROM student_user;  -- 显示为北京时间
```

### 6.3 FastAPI + SQLAlchemy 配置

```python
# database.py
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB

DATABASE_URL = "postgresql://user:password@localhost:5432/ai_learning_tablet"

engine = create_engine(DATABASE_URL)

# 模型示例
class StudentUser(Base):
    __tablename__ = "student_user"
    
    id = Column(BigInteger, primary_key=True)
    phone = Column(String(20), unique=True, nullable=False)
    study_profile = Column(JSONB)  # 使用 JSONB 类型
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

### 6.4 推荐的 Python 库

| 库 | 用途 |
|---|------|
| `asyncpg` | 高性能异步 PostgreSQL 驱动 |
| `SQLAlchemy 2.0` | ORM，支持异步 |
| `alembic` | 数据库迁移 |
| `databases` | 异步数据库接口 |

```python
# 使用 asyncpg + SQLAlchemy 异步
DATABASE_URL = "postgresql+asyncpg://user:pass@localhost/db"

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

engine = create_async_engine(DATABASE_URL)
```

---

**文档结束**
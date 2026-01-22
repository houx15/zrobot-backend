"""
Script to create test users for development and ensure parent/student binding.

Usage:
    cd backend
    python -m scripts.create_test_user
"""
import asyncio
import argparse
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from app.database import async_session_maker, engine, Base
from app.models.user import StudentUser, ParentUser
from app.models.binding import ParentStudentBinding
from app.utils.security import get_password_hash


async def create_tables():
    """Create all tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Tables created successfully")


async def create_or_get_student(
    session, phone: str, password: str, nickname: str, create_missing: bool
):
    result = await session.execute(select(StudentUser).where(StudentUser.phone == phone))
    student = result.scalar_one_or_none()

    if student:
        print(f"Test student already exists: id={student.id}")
        return student

    if not create_missing:
        print(f"Student not found: phone={phone}")
        return None

    student = StudentUser(
        phone=phone,
        password_hash=get_password_hash(password),
        nickname=nickname,
        grade="senior_1",
        personality="活泼开朗，喜欢数学",
        study_profile={
            "weak_subjects": ["english"],
            "strong_subjects": ["math", "physics"],
            "learning_style": "visual",
            "avg_daily_duration": 60,
            "total_questions_solved": 0,
            "accuracy_rate": 0.0,
        },
    )
    session.add(student)
    await session.commit()
    await session.refresh(student)
    print(f"Created test student: id={student.id}, phone={phone}, password={password}")
    return student


async def create_or_get_parent(session, phone: str, nickname: str, create_missing: bool):
    result = await session.execute(select(ParentUser).where(ParentUser.phone == phone))
    parent = result.scalar_one_or_none()

    if parent:
        print(f"Test parent already exists: id={parent.id}")
        return parent

    if not create_missing:
        print(f"Parent not found: phone={phone}")
        return None

    parent = ParentUser(
        phone=phone,
        nickname=nickname,
    )
    session.add(parent)
    await session.commit()
    await session.refresh(parent)
    print(f"Created test parent: id={parent.id}, phone={phone}")
    return parent


async def ensure_binding(session, parent_id: int, student_id: int, relation: str):
    result = await session.execute(
        select(ParentStudentBinding).where(
            ParentStudentBinding.parent_id == parent_id,
            ParentStudentBinding.student_id == student_id,
            ParentStudentBinding.status == 1,
        )
    )
    existing_binding = result.scalar_one_or_none()

    if existing_binding:
        print(f"Binding already exists: id={existing_binding.id}")
        return existing_binding

    binding = ParentStudentBinding(
        parent_id=parent_id,
        student_id=student_id,
        relation=relation,
        status=1,
    )
    session.add(binding)
    await session.commit()
    await session.refresh(binding)
    print(f"Created binding: id={binding.id}")
    return binding


async def create_test_users(args: argparse.Namespace):
    """Create test users and ensure binding"""
    async with async_session_maker() as session:
        student = await create_or_get_student(
            session,
            phone=args.student_phone,
            password=args.student_password,
            nickname=args.student_nickname,
            create_missing=args.create_missing,
        )
        parent = await create_or_get_parent(
            session,
            phone=args.parent_phone,
            nickname=args.parent_nickname,
            create_missing=args.create_missing,
        )
        if not student or not parent:
            print("Skip binding: missing student or parent.")
            return

        await ensure_binding(session, parent.id, student.id, args.relation)

        print("\n" + "=" * 50)
        print("Test accounts ready:")
        print("=" * 50)
        print(f"Student: phone={args.student_phone}, password={args.student_password}")
        print(f"Parent:  phone={args.parent_phone}")
        print(f"Relation: {args.relation}")
        print("=" * 50)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create test users and ensure parent/student binding."
    )
    parser.add_argument("--student-phone", default="13800138000")
    parser.add_argument("--student-password", default="123456")
    parser.add_argument("--student-nickname", default="小明")
    parser.add_argument("--parent-phone", default="13900139000")
    parser.add_argument("--parent-nickname", default="小明妈妈")
    parser.add_argument("--relation", default="mother")
    parser.add_argument(
        "--create-missing",
        action="store_true",
        help="Create student/parent if they do not exist.",
    )
    parser.add_argument("--skip-create-tables", action="store_true")
    return parser.parse_args()


async def main():
    args = parse_args()
    if not args.skip_create_tables:
        print("Creating tables...")
        await create_tables()

    print("\nCreating test users...")
    await create_test_users(args)


if __name__ == "__main__":
    asyncio.run(main())

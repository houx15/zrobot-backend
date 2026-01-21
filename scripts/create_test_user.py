"""
Script to create test users for development

Usage:
    cd backend
    python -m scripts.create_test_user
"""
import asyncio
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


async def create_test_users():
    """Create test users"""
    async with async_session_maker() as session:
        # Check if test student already exists
        result = await session.execute(
            select(StudentUser).where(StudentUser.phone == "13800138000")
        )
        existing_student = result.scalar_one_or_none()

        if existing_student:
            print(f"Test student already exists: id={existing_student.id}")
            student = existing_student
        else:
            # Create test student
            student = StudentUser(
                phone="13800138000",
                password_hash=get_password_hash("123456"),
                nickname="小明",
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
            print(f"Created test student: id={student.id}, phone=13800138000, password=123456")

        # Check if test parent already exists
        result = await session.execute(
            select(ParentUser).where(ParentUser.phone == "13900139000")
        )
        existing_parent = result.scalar_one_or_none()

        if existing_parent:
            print(f"Test parent already exists: id={existing_parent.id}")
            parent = existing_parent
        else:
            # Create test parent
            parent = ParentUser(
                phone="13900139000",
                nickname="小明妈妈",
            )
            session.add(parent)
            await session.commit()
            await session.refresh(parent)
            print(f"Created test parent: id={parent.id}, phone=13900139000")

        # Check if binding already exists
        result = await session.execute(
            select(ParentStudentBinding).where(
                ParentStudentBinding.parent_id == parent.id,
                ParentStudentBinding.student_id == student.id,
                ParentStudentBinding.status == 1,
            )
        )
        existing_binding = result.scalar_one_or_none()

        if existing_binding:
            print(f"Binding already exists: id={existing_binding.id}")
        else:
            # Create binding
            binding = ParentStudentBinding(
                parent_id=parent.id,
                student_id=student.id,
                relation="mother",
                status=1,
            )
            session.add(binding)
            await session.commit()
            await session.refresh(binding)
            print(f"Created binding: id={binding.id}")

        print("\n" + "=" * 50)
        print("Test accounts ready:")
        print("=" * 50)
        print(f"Student: phone=13800138000, password=123456")
        print(f"Parent:  phone=13900139000")
        print("=" * 50)


async def main():
    print("Creating tables...")
    await create_tables()

    print("\nCreating test users...")
    await create_test_users()


if __name__ == "__main__":
    asyncio.run(main())

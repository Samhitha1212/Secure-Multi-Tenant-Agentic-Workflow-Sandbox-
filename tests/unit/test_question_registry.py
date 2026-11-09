"""Tests for the platform question registry."""

import pytest

from secure_agentic_sandbox.execution_config import ExecutionConfig
from secure_agentic_sandbox.question_registry import (
    QuestionDefinition,
    QuestionNotFoundError,
    QuestionRegistry,
    QuestionRegistryError,
    create_default_question_registry,
)


def test_default_registry_contains_three_questions():
    registry = create_default_question_registry()

    assert len(registry) == 3

    questions = registry.list_questions()

    assert {question.id for question in questions} == {
        "python-list-sum",
        "inventory-reservation",
        "inventory-python-fulfillment",
    }

def test_python_question_has_starter_code_and_no_tools():
    registry = create_default_question_registry()

    question = registry.get("python-list-sum")

    assert question.type == "python"
    assert question.starter_code is not None
    assert "sum(numbers)" in question.starter_code
    assert question.allowed_tools == frozenset()
    assert question.starter_workflow is None


def test_workflow_question_defines_its_allowed_tools():
    registry = create_default_question_registry()

    question = registry.get("inventory-reservation")

    assert question.type == "workflow"
    assert question.allowed_tools == {
        "inventory.lookup",
        "inventory.reserve",
    }
    assert question.starter_workflow is not None
    assert len(question.starter_workflow["steps"]) == 2


def test_question_detail_contains_problem_and_limits():
    registry = create_default_question_registry()

    detail = registry.get("inventory-reservation").to_detail_dict()

    assert detail["statement"]
    assert detail["requirements"]
    assert detail["resource_limits"]["cpu"] == "500m"
    assert detail["resource_limits"]["memory"] == "512Mi"


def test_unknown_question_is_rejected():
    registry = create_default_question_registry()

    with pytest.raises(QuestionNotFoundError):
        registry.get("does-not-exist")


def test_duplicate_question_ids_are_rejected():
    question = QuestionDefinition(
        id="test-question",
        title="Test question",
        type="python",
        description="Test description",
        statement="Test statement",
        requirements=("Print a result.",),
        starter_code="print('hello')",
    )

    with pytest.raises(
        QuestionRegistryError,
        match="Duplicate question ID",
    ):
        QuestionRegistry((question, question))


def test_invalid_question_id_is_rejected():
    with pytest.raises(QuestionRegistryError, match="Invalid question ID"):
        QuestionDefinition(
            id="Invalid ID!",
            title="Test question",
            type="python",
            description="Test description",
            statement="Test statement",
            requirements=("Print a result.",),
            starter_code="print('hello')",
        )


def test_python_question_cannot_assign_tools():
    with pytest.raises(
        QuestionRegistryError,
        match="cannot define workflow tools",
    ):
        QuestionDefinition(
            id="python-with-tools",
            title="Invalid question",
            type="python",
            description="Test description",
            statement="Test statement",
            requirements=("Run Python.",),
            allowed_tools=frozenset({"inventory.lookup"}),
            starter_code="print('hello')",
        )


def test_registry_returns_independent_starter_workflow():
    registry = create_default_question_registry()

    first = registry.get("inventory-reservation").to_detail_dict()
    first["starter_workflow"]["steps"].clear()

    second = registry.get("inventory-reservation").to_detail_dict()

    assert len(second["starter_workflow"]["steps"]) == 2


def test_invalid_resource_configuration_is_rejected():
    with pytest.raises(ValueError):
        ExecutionConfig(
            cpu="5000m",
            memory="512Mi",
            timeout_seconds=10,
        )

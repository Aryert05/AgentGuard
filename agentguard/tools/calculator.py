"""Validated arithmetic operations."""
from __future__ import annotations

from typing import Literal, Union
from pydantic import BaseModel, Field


class CalculatorArgs(BaseModel):
    operation: Literal["add", "subtract", "multiply", "divide"]
    a: float = Field(allow_inf_nan=False)
    b: float = Field(allow_inf_nan=False)


def calculate(operation: str, a: float, b: float) -> dict[str, Union[float, str]]:
    args = CalculatorArgs(operation=operation, a=a, b=b)
    if args.operation == "divide" and args.b == 0:
        raise ZeroDivisionError("Cannot divide by zero")
    result = {"add": args.a + args.b, "subtract": args.a - args.b,
              "multiply": args.a * args.b, "divide": args.a / args.b if args.b else 0}[args.operation]
    return {"operation": args.operation, "a": args.a, "b": args.b, "result": result}

import asyncio
from datetime import timedelta

from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig


async def main() -> None:
    config = ConnectionConfig(
        domain="127.0.0.1:8080",
        use_server_proxy=True,
    )

    sandbox = await Sandbox.create(
        "python:3.11",
        connection_config=config,
        timeout=timedelta(minutes=2),
        resource={"cpu": "500m", "memory": "512Mi"},
        metadata={"purpose": "sdk-smoke-test"},
    )

    try:
        result = await sandbox.commands.run(
            "python -u -c 'print(6 * 7)'"
        )

        print("Sandbox ID:", sandbox.id)
        print(
            "STDOUT:",
            "".join(item.text for item in result.logs.stdout),
        )
        print(
            "STDERR:",
            "".join(item.text for item in result.logs.stderr),
        )
        print(
            "Result attributes:",
            [name for name in dir(result) if not name.startswith("_")],
        )
    finally:
        await sandbox.destroy()


if __name__ == "__main__":
    asyncio.run(main())

"""写入模式智能体 — 由自动化脚本调用

支持 CLI 和 Python API 两种调用方式。

CLI:
    python writer_agent.py /path/to/project --task "为所有实体补充 brief"

Python API:
    from writer_agent import create_writer_agent, run_task
    agent = create_writer_agent("/path/to/project")
    result = agent.chat("检查所有实体的 brief 字段")
"""
import argparse
import sys

from agent.agent import PontisAgent, AgentSpec
from agent.tools import build_writer_registry
from agent.prompt import build_prompt


def create_writer_agent(project_path: str) -> PontisAgent:
    """创建写入模式智能体。"""
    tools = build_writer_registry()
    prompt = build_prompt(AgentSpec(mode="writer", project_path=project_path))
    return PontisAgent(project_path, tools=tools, system_prompt=prompt)


def run_task(project_path: str, task: str) -> str:
    """执行单次写入任务。"""
    agent = create_writer_agent(project_path)
    return agent.chat(task)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pontis 写入模式智能体")
    parser.add_argument("project_path", help="项目目录路径")
    parser.add_argument("--task", required=True, help="要执行的任务描述")
    args = parser.parse_args()

    result = run_task(args.project_path, args.task)
    print(result)

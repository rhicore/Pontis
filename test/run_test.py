"""
Agent 测试脚本 — 记录完整调用链路到 Markdown 日志文件。

Usage:
    python test/run_test.py [project_path] [question]
    python test/run_test.py example_data/context "告诉我这个项目是做什么的"
    python test/run_test.py example_data/context  # 使用默认问题列表
"""
import json
import os
import sys
import time
from datetime import datetime

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.agent import PontisAgent


class CallLogger:
    """包装 PontisAgent，记录每一轮 LLM 调用的完整输入输出到 Markdown。"""

    def __init__(self, agent: PontisAgent, log_path: str):
        self.agent = agent
        self.log_path = log_path
        self.call_round = 0

    def _log(self, text: str = ""):
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(text + "\n")

    def chat_with_log(self, user_input: str) -> str:
        """执行一次 chat，记录完整调用链路。"""
        self.call_round = 0

        self._log(f"## 💬 用户提问\n")
        self._log(f"> {user_input}")
        self._log(f"\n*{datetime.now().isoformat()}*\n")

        # 注入消息
        self.agent.messages.append({"role": "user", "content": user_input})

        # 记录初始上下文
        self._log(f"<details><summary>📄 初始上下文（发送给模型的消息列表）</summary>\n")
        self._log_context_messages()
        self._log(f"\n</details>\n")

        # 工具调用循环
        while True:
            self.call_round += 1
            self._log(f"### Round {self.call_round}\n")

            # 记录 messages 摘要
            self._log_messages_summary()

            t0 = time.time()
            response = self.agent.client.chat.completions.create(
                model=self.agent.config.model,
                messages=self.agent.messages,
                tools=TOOL_DEFINITIONS,
                max_tokens=self.agent.config.max_tokens,
                temperature=self.agent.config.temperature,
            )
            elapsed = time.time() - t0

            msg = response.choices[0].message
            self.agent.messages.append(msg.to_dict())

            # 记录 LLM 响应
            self._log(f"**LLM 响应** — 耗时 {elapsed:.1f}s | finish_reason: `{response.choices[0].finish_reason}`\n")

            if msg.content:
                self._log(f"<details><summary>📝 模型输出文本</summary>\n")
                self._log(msg.content)
                self._log(f"\n</details>\n")

            if msg.tool_calls:
                self._log(f"**工具调用** ({len(msg.tool_calls)} 个):\n")
                for tc in msg.tool_calls:
                    self._log(f"- `{tc.function.name}({tc.function.arguments})`")
                self._log("")

            # 如果没有工具调用，返回文本
            if not msg.tool_calls:
                self._log(f"---\n*对话结束（模型返回文本）*\n")
                return msg.content or ""

            # 执行工具调用并记录
            for tool_call in msg.tool_calls:
                name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                result = execute_tool(name, arguments, self.agent.project_path)
                if len(result) > 8000:
                    result = result[:8000] + "\n... (truncated)"

                self._log(f"<details><summary>🔧 {name}({json.dumps(arguments, ensure_ascii=False)}) → {len(result)} 字符</summary>\n")
                self._log(f"\n**参数:**\n```json\n{json.dumps(arguments, ensure_ascii=False, indent=2)}\n```\n")
                self._log(f"\n**返回:**\n```\n{result}\n```\n")
                self._log(f"</details>\n")

                self.agent.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

    def _log_context_messages(self):
        """记录完整的消息上下文。"""
        for i, m in enumerate(self.agent.messages):
            role = m.get("role", "?")
            content = m.get("content", "")
            tool_call_id = m.get("tool_call_id", "")

            if role == "system":
                self._log(f"\n**[{i}] system** ({len(content)} 字符):\n")
                self._log(f"```\n{content}\n```\n")
            elif role == "user":
                self._log(f"\n**[{i}] user:** {content}\n")
            elif role == "tool":
                self._log(f"\n**[{i}] tool** (call_id=`{tool_call_id}`, {len(content)} 字符):\n")
                self._log(f"<details><summary>tool 返回内容</summary>\n\n```\n{content}\n```\n\n</details>\n")
            else:
                # assistant message
                text = content or ""
                tcs = m.get("tool_calls", [])
                self._log(f"\n**[{i}] assistant:**\n")
                if text:
                    self._log(f"  text: {text}")
                if tcs:
                    for tc in tcs:
                        fn = tc.get("function", {})
                        self._log(f"  tool_call: `{fn.get('name', '?')}({fn.get('arguments', '')})`")
                self._log("")

    def _log_messages_summary(self):
        """简要记录当前 messages 列表。"""
        total_chars = sum(len(str(m.get("content", ""))) for m in self.agent.messages)
        self._log(f"*Messages: {len(self.agent.messages)} 条, 上下文 ~{total_chars} 字符*\n")


def run_test(project_path: str, questions: list[str]):
    """运行测试问题并记录日志。"""
    os.makedirs("test", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"test/agent_{timestamp}.md"

    # 初始化 agent
    agent = PontisAgent(project_path)
    logger = CallLogger(agent, log_path)

    # 写 Markdown 头部
    logger._log(f"# Agent 测试报告\n")
    logger._log(f"- **时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger._log(f"- **模型**: `{agent.config.model}`")
    logger._log(f"- **Provider**: `{agent.config.provider}`")
    logger._log(f"- **max_tokens**: {agent.config.max_tokens}")
    logger._log(f"- **temperature**: {agent.config.temperature}")
    logger._log(f"- **项目路径**: `{project_path}`\n")

    # 记录 system prompt
    logger._log(f"<details><summary>🧠 System Prompt ({len(agent.system_prompt)} 字符)</summary>\n\n")
    logger._log(f"```\n{agent.system_prompt}\n```\n\n</details>\n")

    # 记录工具定义
    logger._log(f"<details><summary>🛠️ 工具定义 ({len(TOOL_DEFINITIONS)} 个)</summary>\n\n")
    for tool_def in TOOL_DEFINITIONS:
        fn = tool_def["function"]
        logger._log(f"**`{fn['name']}`**\n")
        logger._log(f"```\n{fn['description']}\n```\n")
        logger._log(f"参数: `{json.dumps(fn['parameters'], ensure_ascii=False)}`\n")
    logger._log(f"\n</details>\n")

    logger._log("---\n")

    # 逐个问题测试
    print(f"项目: {project_path}")
    print(f"日志: {log_path}")
    print()

    for i, q in enumerate(questions):
        print(f"\n[{i+1}/{len(questions)}] {q}")
        response = logger.chat_with_log(q)
        print(f"  → {response[:150]}{'...' if len(response) > 150 else ''}")
        logger._log("---\n")

    print(f"\n日志已保存: {log_path}")
    return log_path


if __name__ == "__main__":
    # late import 避免循环
    from agent.tools import TOOL_DEFINITIONS, execute_tool

    project = sys.argv[1] if len(sys.argv) > 1 else "example_data/context"
    custom_q = sys.argv[2] if len(sys.argv) > 2 else None

    default_questions = [
        "告诉我这个项目是做什么的",
        "event表里有哪些字段，分别是什么含义",
        "哪个列的NULL比例最高",
        "费用记录里最常见的是什么类型的支出",
    ]

    questions = [custom_q] if custom_q else default_questions
    run_test(project, questions)

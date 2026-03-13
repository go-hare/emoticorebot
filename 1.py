from langchain.chat_models import init_chat_model
from deepagents import create_deep_agent
from typing import Literal
from langchain.agents import create_agent
model = init_chat_model(
    model="gpt-5.4",
    base_url = "https://ai.imgwwo.top/v1",
    api_key = "sk-umcbxfarpNQBM8PosX1KekWJs2gGezeeUHRUN7XOOZvt1aP0"
)

def get_weather(city: str) -> str:
    """Get weather for a given city."""
    return f"It's always sunny in {city}!"


def get_wedu(city: str) -> str:
    """根据 城市获取 温度"""
    print(2222222222222)
    return f" 49 度"

get_wedu_subagent = {
    "name": "get_wedu_subagent",
    "description": "获取天气温度",
    "system_prompt": "城市温度助手",
    "tools": [get_wedu,get_weather],
    "model": model,
}

subagents = [get_wedu_subagent]

# agent = create_deep_agent(model=model,system_prompt="个人助手",subagents= subagents)
agent = create_agent(model=model,system_prompt="个人助手",tools= [get_wedu,get_weather])

result = agent.invoke(
    {"messages": [{"role": "user", "content": "杭州的天气和温度"}]},
)

response_messages = result.get("messages", [])
print(response_messages)
print(response_messages[-1].content)
# tool_items = []
# agent_items = []
# for item in data:
#     if item["type"] == "updates":
#         for node_name, state in item["data"].items():
#             print(f"Node {node_name} updated: {state}")
#     elif item["type"] == "custom":
#         print(f"Status: {item['data']['status']}")
# print(tool_items)
# # print(agent_items)  
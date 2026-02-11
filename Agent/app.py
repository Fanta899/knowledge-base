import streamlit as st
from openai import OpenAI

# --- 页面配置 ---
st.set_page_config(page_title="C++ 面试 Agent", layout="wide")

# --- 侧边栏：用户配置 ---
with st.sidebar:
    st.title("⚙️ 配置中心")
    st.markdown("请使用您自己的 API Key 来启动面试。")
    
    api_key = st.text_input("API Key", type="password", help="在此输入您的 OpenAI 或 DeepSeek Key")
    base_url = st.text_input("Base URL", value="https://api.deepseek.com", help="API 的基础地址")
    model_name = st.text_input("Model Name", value="deepseek-chat")
    
    if st.button("清空聊天记录"):
        st.session_state.messages = []
        st.rerun()

st.title("🤖 C++ 资深面试官")
st.caption("基于 LLM 的智能面试系统 - 专注于 C++ 底层、STL 及并发编程")

# --- 初始化聊天历史 ---
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "system", "content": "你是一位严厉的 C++ 面试官。你会根据候选人的回答进行深度追问，侧重于内存模型、虚函数、智能指针、STL 源码实现等。"}
    ]

# --- 渲染聊天对话框 ---
for message in st.session_state.messages:
    if message["role"] != "system":
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

# --- 核心逻辑：用户输入 ---
if prompt := st.chat_input("输入你的回答..."):
    # 检查 Key
    if not api_key:
        st.error("请先在左侧侧边栏填入 API Key！")
        st.stop()

    # 显示用户消息
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    # 调用 AI
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        
        with st.chat_message("assistant"):
            response_placeholder = st.empty() # 用于流式输出
            full_response = ""
            
            # 使用流式传输 (Stream)，体验更好
            completion = client.chat.completions.create(
                model=model_name,
                messages=st.session_state.messages,
                stream=True,
            )
            
            for chunk in completion:
                if chunk.choices[0].delta.content:
                    full_response += chunk.choices[0].delta.content
                    response_placeholder.markdown(full_response + "▌")
            
            response_placeholder.markdown(full_response)
        
        st.session_state.messages.append({"role": "assistant", "content": full_response})
        
    except Exception as e:
            # 捕捉额度不足的错误
            if "insufficient_quota" in str(e):
                st.error("🚫 余额不足：您的 API Key 额度已耗尽或已过期，请检查账户余额。")
            elif "invalid_api_key" in str(e):
                st.error("🔑 Key 错误：您输入的 API Key 无效，请重新检查。")
            else:
                st.error(f"❌ 发生错误: {str(e)}")

from __future__ import annotations

# Chinese desktop-style entrypoint for the Streamlit research UI.

import streamlit as st

from quant_mm.ui_labels import translate_dataframe, translate_text


st.set_page_config(
    page_title="Crypto量化研究平台",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.set_page_config = lambda *args, **kwargs: None

_original_dataframe = st.dataframe
_original_title = st.title
_original_caption = st.caption
_original_markdown = st.markdown
_original_slider = st.slider
_original_bar_chart = st.bar_chart


def _dataframe(data=None, *args, **kwargs):
    return _original_dataframe(
        translate_dataframe(data, fill_missing=True),
        *args,
        **kwargs,
    )


def _title(body, *args, **kwargs):
    return _original_title(translate_text(body), *args, **kwargs)


def _caption(body, *args, **kwargs):
    return _original_caption(translate_text(body), *args, **kwargs)


def _markdown(body, *args, **kwargs):
    return _original_markdown(translate_text(body), *args, **kwargs)


def _slider(label, *args, **kwargs):
    return _original_slider(translate_text(label), *args, **kwargs)


def _bar_chart(data=None, *args, **kwargs):
    if "y_label" in kwargs:
        kwargs["y_label"] = translate_text(kwargs["y_label"])
    return _original_bar_chart(translate_dataframe(data), *args, **kwargs)


st.dataframe = _dataframe
st.title = _title
st.caption = _caption
st.markdown = _markdown
st.slider = _slider
st.bar_chart = _bar_chart

pages = [
    st.Page("pages/00_Quick_Direction.py", title="快速多空预测", icon="⚡", default=True),
    st.Page("pages/02_Gate_Event_Contracts.py", title="Gate 事件合约", icon="🎯"),
    st.Page("dashboard.py", title="详细预测研究", icon="📈"),
    st.Page("pages/01_Institutional_Alpha.py", title="机构 Alpha 研究", icon="🧠"),
]

navigation = st.navigation(pages, position="sidebar")
navigation.run()

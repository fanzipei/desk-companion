"""Proxy policy shared by news feeds, article extraction and related searches."""
from urllib.parse import urlsplit
from urllib.request import ProxyHandler

PROXY_MODES={"auto":"自动（系统 / 直连）","custom":"自定义代理","direct":"不使用代理"}


def validate_proxy(config):
    mode=config.get("news_proxy_mode","auto")
    url=config.get("news_proxy_url","")
    if mode not in PROXY_MODES or not isinstance(url,str):
        raise ValueError("请选择有效的新闻代理方式。")
    if mode=="custom" and not url:
        raise ValueError("请填写新闻代理地址，例如 http://127.0.0.1:7897。")
    if url:
        try:
            parsed=urlsplit(url)
            valid=(parsed.scheme in ("http","https") and parsed.hostname and parsed.port
                and 1<=parsed.port<=65535 and parsed.path in ("","/")
                and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
                and not any(c.isspace() or ord(c)<32 for c in url))
        except ValueError:
            valid=False
        if not valid:
            raise ValueError("代理地址请用 http://主机:端口 或 https://主机:端口；"
                "请使用代理软件的 HTTP/混合端口，不支持 SOCKS、账号密码或查询参数。")


def connection_routes(config=None, preferred=None):
    config=config or {}
    validate_proxy(config)
    mode=config.get("news_proxy_mode","auto")
    if mode!="auto":
        return [mode]  # Explicit custom/direct choices never silently fall back.
    return list(dict.fromkeys(([preferred] if preferred in ("system","direct") else [])+["system","direct"]))


def proxy_handler(route, config=None):
    if route=="direct":
        return ProxyHandler({})
    if route=="custom":
        config=config or {}
        validate_proxy(config)
        url=config["news_proxy_url"]
        return ProxyHandler({"http":url,"https":url})
    return ProxyHandler()

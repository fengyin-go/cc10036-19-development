"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import layout_imports, resource

# layout_imports 必须排在 resource 之前：/api/layout-imports 是固定前缀，
# 而 resource 的 /api/{module} 会把任何首段路径都当成模块 key。
ROUTERS = [layout_imports, resource]

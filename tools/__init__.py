"""业务工具集合。

导入本包就会按下列顺序触发所有内置工具的 @tool 注册，
注册结果落到 ``ai_agent.tools._REGISTRY``。

agent.py 只需 ``import tools`` 一行，即可让 18 个工具全部就绪。

⚠️ 与 ``ai_agent.tools`` 不同：
- ``ai_agent.tools`` 是工具系统本身（@tool 装饰器、_REGISTRY、build_tool_meta）
- ``tools/``（本包）是业务工具实现（calc / web / files / memory / skills 等）
"""

from . import basic          # noqa: F401  - 基础工具
from . import execute        # noqa: F401  - execute_code（沙箱）
from . import memory_tools   # noqa: F401  - 长期记忆（5 个 + 撤销 3 个）
from . import files          # noqa: F401  - 文件读写编辑（5 个）
from . import skills         # noqa: F401  - 持久化技能（3 个 + restore） + 启动时加载已有技能
from . import todo           # noqa: F401  - 对话级 todo 清单（2 个）
from . import dialog         # noqa: F401  - 主动追问 ask_user（1 个）
from . import vision         # noqa: F401  - 视觉识别 vision_describe（1 个，调 MiMo）
from . import shell          # noqa: F401  - 系统命令白名单 run_command（1 个）
from . import self_edit      # noqa: F401  - 自我修改 self_read/edit/write/rollback/diff（5 个）
from . import spawn_sub      # noqa: F401  - 主对话里 spawn_sub_conversation（1 个）
from . import ui_check       # noqa: F401  - screenshot_and_describe UI 自检（1 个）
from . import plan           # noqa: F401  - plan_task 复杂任务先想再做（1 个）
from . import verify         # noqa: F401  - verify_change 改完断言关键 pattern（1 个）
from . import postmortem     # noqa: F401  - write_postmortem 任务复盘 + 自动 inject（1 个）
from . import aggregate_search  # noqa: F401  - aggregate_search 多引擎聚合搜索（1 个）

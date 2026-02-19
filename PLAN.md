# NovelAI 生图功能实施方案

## 功能概述
添加 `/novelai生图` 斜杠命令，调用 NovelAI 官方 API 生成图片。支持画师串、正面/负面提示词、氛围转移(Vibe Transfer)、图片尺寸等参数。提供画师串预设收藏系统和 Dashboard 模型配置。

---

## 文件变更清单

### 1. `src/chat/config/chat_config.py` — 添加 NovelAI 配置
在 `VIDEO_GEN_CONFIG` 后面添加 `NOVELAI_CONFIG` 配置块：
```python
def _get_novelai_config():
    return {
        "ENABLED": _parse_bool_env("NOVELAI_ENABLED", "False"),
        "API_TOKEN": os.getenv("NOVELAI_API_TOKEN", ""),   # NovelAI Persistent API Token
        "API_URL": os.getenv("NOVELAI_API_URL", "https://image.novelai.net"),
        "MODEL": os.getenv("NOVELAI_MODEL", "nai-diffusion-4-full"),
        "DEFAULT_SAMPLER": os.getenv("NOVELAI_SAMPLER", "k_euler_ancestral"),
        "DEFAULT_STEPS": int(os.getenv("NOVELAI_STEPS", "28")),
        "DEFAULT_SCALE": float(os.getenv("NOVELAI_SCALE", "6.5")),
        "DEFAULT_WIDTH": int(os.getenv("NOVELAI_WIDTH", "832")),
        "DEFAULT_HEIGHT": int(os.getenv("NOVELAI_HEIGHT", "1216")),
        "UC_PRESET": int(os.getenv("NOVELAI_UC_PRESET", "0")),
        "QUALITY_TOGGLE": _parse_bool_env("NOVELAI_QUALITY_TOGGLE", "True"),
        "GENERATION_COST": int(os.getenv("NOVELAI_COST", "5")),
        "SMEA": _parse_bool_env("NOVELAI_SMEA", "False"),
        "SMEA_DYN": _parse_bool_env("NOVELAI_SMEA_DYN", "False"),
        "CFG_RESCALE": float(os.getenv("NOVELAI_CFG_RESCALE", "0")),
        "NOISE_SCHEDULE": os.getenv("NOVELAI_NOISE_SCHEDULE", "native"),
    }

NOVELAI_CONFIG = _get_novelai_config()

def reload_novelai_config():
    global NOVELAI_CONFIG
    NOVELAI_CONFIG.update(_get_novelai_config())
    return NOVELAI_CONFIG
```

### 2. `src/chat/utils/database.py` — 添加画师串预设表
在 `_create_tables()` 中添加新表：
```sql
CREATE TABLE IF NOT EXISTS novelai_artist_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,         -- Discord 用户 ID
    name TEXT NOT NULL,               -- 预设名称
    artist_string TEXT NOT NULL,      -- 画师串内容
    is_global INTEGER DEFAULT 0,      -- 1=全局预设(管理员创建), 0=个人预设
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, name)
);
```
添加 CRUD 方法：
- `add_novelai_preset(user_id, name, artist_string, is_global=False)`
- `get_novelai_presets(user_id)` — 返回用户个人 + 全局预设
- `delete_novelai_preset(user_id, name)`
- `get_novelai_preset_by_name(user_id, name)` — 用于 autocomplete 匹配

### 3. 新文件 `src/chat/features/image_generation/services/novelai_service.py` — 服务层
核心职责：调用 NovelAI API

```python
class NovelAIService:
    """NovelAI 图像生成服务"""

    def __init__(self):
        self._initialize()

    def is_available(self) -> bool
    def update_config(self, **kwargs) -> dict

    async def generate_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        artist_string: str = "",       # 画师串，会与 prompt 拼接
        width: int = 832,
        height: int = 1216,
        steps: int = 28,
        scale: float = 6.5,
        sampler: str = "k_euler_ancestral",
        seed: int = -1,
        reference_images: list[bytes] = None,  # 氛围转移图片
        reference_strengths: list[float] = None,
        reference_info_extracted: list[float] = None,
    ) -> bytes:  # 返回 PNG 图片字节
```

**API 调用细节：**
- 端点: `POST {API_URL}/ai/generate-image`
- Header: `Authorization: Bearer {API_TOKEN}`
- 请求体:
```json
{
    "input": "画师串 + 正面提示词",
    "model": "nai-diffusion-4-full",
    "action": "generate",
    "parameters": {
        "width": 832, "height": 1216,
        "scale": 6.5, "sampler": "k_euler_ancestral",
        "steps": 28, "seed": -1 或随机,
        "n_samples": 1,
        "negative_prompt": "负面提示词",
        "qualityToggle": true, "ucPreset": 0,
        "sm": false, "sm_dyn": false,
        "cfg_rescale": 0, "noise_schedule": "native",
        "reference_image_multiple": [...base64...],
        "reference_strength_multiple": [0.6],
        "reference_information_extracted_multiple": [1.0]
    }
}
```
- 响应: ZIP 文件（200 状态码），解压后得到 PNG

### 4. 新文件 `src/chat/features/image_generation/cogs/novelai_cog.py` — 斜杠命令
```python
class NovelAICog(commands.Cog):
    """NovelAI 图像生成 Cog"""

    # /novelai生图 — 主命令
    @app_commands.command(name="novelai生图", description="使用 NovelAI 生成图片")
    参数:
      - 正面提示词: str (必填)
      - 画师串: str = "" (可选，支持 autocomplete 从预设中选择)
      - 负面提示词: str = ""
      - 图片尺寸: Choice[竖版832x1216 / 横版1216x832 / 正方形1024x1024 / 手机竖屏768x1344 / 宽屏1344x768]
      - 氛围图片: Attachment = None (可选，上传参考图)
      - 氛围强度: float = 0.6 (Range 0.0~1.0)
      - 信息提取: float = 1.0 (Range 0.0~1.0)
      - 步数: int = 28 (Range 1~50)
      - 引导强度: float = 6.5 (CFG Scale)
      - 采样器: Choice[k_euler_ancestral / k_euler / k_dpmpp_2m / ddim / ...]
      - 种子: int = -1

    # 画师串 autocomplete — 从数据库预设中搜索匹配
    @画师串.autocomplete
    async def artist_autocomplete(self, interaction, current)

    # /novelai预设 管理 — 命令组
    @app_commands.command(name="novelai预设", description="管理画师串预设")
    子命令:
      - 保存: 保存当前画师串为预设
      - 删除: 删除一个预设
      - 列表: 查看所有预设
```

**Autocomplete 逻辑：**
- 用户在 `画师串` 参数输入时，自动从数据库查询匹配的预设
- 选择预设后，自动填充对应的画师串内容
- 也支持直接输入自定义画师串

### 5. `src/dashboard/api.py` — 添加 NovelAI 配置 API 端点

添加 Pydantic 模型：
```python
class NovelAIConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    api_token: Optional[str] = None
    api_url: Optional[str] = None
    model: Optional[str] = None
    default_sampler: Optional[str] = None
    default_steps: Optional[int] = None
    default_scale: Optional[float] = None
    default_width: Optional[int] = None
    default_height: Optional[int] = None
    generation_cost: Optional[int] = None
    quality_toggle: Optional[bool] = None
    smea: Optional[bool] = None
    smea_dyn: Optional[bool] = None
    cfg_rescale: Optional[float] = None
    noise_schedule: Optional[str] = None
    uc_preset: Optional[int] = None
```

端点：
- `GET /api/config/novelai` — 获取 NovelAI 配置
- `PUT /api/config/novelai` — 更新 NovelAI 配置（热重载服务）
- `POST /api/config/test-novelai` — 测试 API 连接

### 6. `src/dashboard/static/index.html` — 添加 NovelAI 设置页面

**导航栏：** 在 video 后面添加 novelai tab 按钮

**Alpine.js 数据：**
```javascript
novelaiForm: {
    enabled: false, api_token: '', api_url: '', model: 'nai-diffusion-4-full',
    default_sampler: 'k_euler_ancestral', default_steps: 28, default_scale: 6.5,
    default_width: 832, default_height: 1216, generation_cost: 5,
    quality_toggle: true, smea: false, smea_dyn: false,
    cfg_rescale: 0, noise_schedule: 'native', uc_preset: 0
},
```

**设置页面包含：**
1. 服务启用/禁用开关
2. API Token 输入框（密码类型）
3. API URL（默认 https://image.novelai.net）
4. 模型选择（下拉 + 自定义输入）
   - 预设: nai-diffusion-4-full, nai-diffusion-4-curated, nai-diffusion-4.5-full, nai-diffusion-4.5-curated, nai-diffusion-3
5. 默认采样器选择
6. 默认步数/CFG Scale/尺寸
7. SMEA / SMEA+DYN 开关
8. CFG Rescale 数值
9. 生成成本（月光币）
10. UC Preset 选择
11. 测试连接按钮
12. 保存按钮

**syncForms：** 在 `syncForms()` 中添加加载 NovelAI 配置的 fetch 调用

---

## 执行顺序

1. **chat_config.py** — 添加 `NOVELAI_CONFIG`
2. **database.py** — 添加画师串预设表 + CRUD
3. **novelai_service.py** — 新建服务层
4. **novelai_cog.py** — 新建斜杠命令 Cog
5. **dashboard/api.py** — 添加后端 API
6. **dashboard/static/index.html** — 添加前端页面

---

## NovelAI API 参考

- 端点: `POST https://image.novelai.net/ai/generate-image`
- 认证: `Authorization: Bearer {Persistent API Token}`
- 响应: `201 Created` → `application/zip` (解压后为 PNG)
- 可用模型: nai-diffusion-4-full, nai-diffusion-4-curated, nai-diffusion-4.5-full, nai-diffusion-4.5-curated, nai-diffusion-3
- 氛围转移: 通过 `reference_image_multiple` (base64数组), `reference_strength_multiple`, `reference_information_extracted_multiple` 实现
- 尺寸必须是 64 的倍数

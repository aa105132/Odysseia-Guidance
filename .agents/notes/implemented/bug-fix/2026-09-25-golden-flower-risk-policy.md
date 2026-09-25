# Agent Note: 炸金花陪玩停止持续盲跟与弱牌接明牌加注

Status: implemented

## Problem

用户反馈玩家看牌后弱牌弃、强牌加，月月仍持续跟注。本地代码能看牌并正确识别牌型，但旧提示鼓励弱牌继续跟注/诈唬，参考胜率主要针对随机三张牌；兜底算法对已看牌玩家只做温和范围收紧。模型出口只校验动作合法性，没有控制连续盲跟或弱牌累计成本。未查询线上模型日志，因此不认定用户某局具体由模型还是兜底产生。

## Decision

将金花（同花，category >= 3）、顺金、豹子定义为强牌，仅用于陪玩策略，不改变牌型顺序或人类玩家的合法操作。

- GoldenFlowerGame 按实际动作时的 seen 状态记录 seen_raises，公开且按局初始化；先盲加后看牌不计为明牌加注，已弃牌者不构成当前压力。
- golden_bot_policy 只读自身已看牌和公开信息。首圈之后、面对明牌加注、基础注超过两倍底注或单次成本超过剩余筹码的 1/30，暗牌付费动作必须先 look。look 不支付筹码、不结束回合，下次重新决策。
- 同花以下遇到仍在局的明牌加注者弃牌；单次继续成本不得超过 min(4 倍底注, 剩余筹码 5%)，包含底注的累计投入不得超过 min(6 倍底注, 初始买入 10%)。底池再大也不放宽这条边界，因为 call 既不结束下注也不改善手牌。
- 模型付费动作先在副本上验证合法，再应用 guard_bot_action，并验证替换动作；会话记实际动作。模型失败/禁用走同一边界的本地 suggest_action。低成本无压力局面保留少量诈唬；价值加注需同花以上。德州策略不变。
- game_llm 白名单增加 seen_raises，strategy.risk_policy 提供匿名同源判断；同步收紧提示，不再只依赖提示词自觉。

## Verification

新增 tests/test_golden_flower_risk.py：六种牌型、看牌前后加注顺序、持续盲跟、不同底注预算、所有付费动作、隐藏信息边界、人类操作不受限、恒定 call 模型与禁用模型的 look→fold、真实执行前不变更牌局、筹码守恒和会话记录。

以下 14 个测试文件合计 **426 passed**：test_golden_flower_risk、test_poker_bot_strategy、test_poker_llm_strategy、test_game_bot_runner、test_game_llm、test_table_poker_games、test_game_llm_integration、test_texas_llm_strategy、test_texas_action_memory、test_game_jev、test_table_room_settings、test_custom_room_caps、test_table_multiplayer_api、test_table_statistics。

初次扩展检查为 381 passed / 45 setup errors，原因是本机缺 cachetools。在会话 scratch/golden-test-deps 临时安装并通过 PYTHONPATH 加载后，同一批 426 全通过；没有修改项目依赖或全局 Python。仍有既有 audioop/FastAPI on_event 弃用警告。测试为本地固定牌局、模拟模型与 API 集成，不代表线上真实模型胜率评估。

## Alternatives considered

- 只改提示词：模型不遵守或客户端回退时仍会持续跟，故使用共享执行边界。
- 所有同花以下立即弃牌：会丢掉对盲牌的小成本机会；保留严格预算内试探。
- 用已看牌状态直接当强牌信号：会误伤先盲加后看牌者；记录加注当时状态。
- 使用对手底牌修正胜率：违反信息边界，拒绝。

## Consequences

减少被紧手玩家用明牌加注持续收取弱牌筹码的情况，并保证两条陪玩路径一致。代价是策略更保守，明确的同花门槛可能被主动诈唬者利用；不是均衡策略，也不保证同花必胜。暂未引入跨局玩家画像或线上学习。公开状态新增兼容字段 seen_raises，无余额、数据库、结算规则修改。

本次未部署、未重启服务；之前宠物靠左的本地 CSS/测试改动保持原样。后端策略上线需要单独受控发布，不能仅替换前端静态文件。

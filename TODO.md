1）如何处理没有配对成功的AGU，权衡好成本和效果
  方案 1，分桶路由 + 桶内排序
  - 先按候选类型分桶：strong AG mismatch、unmatched U/A/G、weak AG、low-confidence/self-ref。
  - 每个桶给固定配额进 phase3，不允许 unmatched 因为低分直接被淹没。
  - 桶内再排优先级，但只决定先看谁，不决定能不能看。
  - 好处是最符合你们的目标：保召回，不会因为“没有成对”就把真实 bug 扔掉。
2）md相关信息全部抽成高优先级guarantee或者reference进入phase2，每个query配对top-k个A或者U然后再配对最多top-k个 reference，一起送给channelb的llm来做finding判断。
3）非跨chunk bug无法通过agu配对的形式成为高优先级候选，以及目前的架构有自排斥限制。
  solution：spec生成式让llm做chunk 内agu自检，一方面进行去重，另一方面给出高危uncertain point
4) 半formal保确定性，codex保效果
5）miss bug的分类：1️⃣没有形成finding（单agu或者无agu或者单chunk内）；2️⃣finding权重低；3️⃣有finding但是判断错误
# MAX-Q 写实素材生成指南(Gemini / Nano Banana Pro)

把这份文件里的 prompt 原样喂给 Gemini,生成的 PNG 按下面的路径放好,
重启 MAX-Q(`pkill -f maxq.py && ./run.sh`)就会自动替换程序绘制的火箭。
**火焰、烟雾、震屏永远是程序实时画的**——所以静态图也会"活"。

```
assets/vehicle/stack.png     整箭直立(助推器+飞船),竖构图
assets/vehicle/booster.png   只有助推器(接住用)
assets/vehicle/tower.png     发射塔(不要画机械臂!筷子是程序画的,要动)
assets/boss/<state>.png      可选:统一画风的人物肖像(见第 3 节)
```

---

## 0. 通用规则(每条 prompt 前面都带上)

> Render on a SOLID PURE GREEN background (#00FF00), no shadows cast on
> the background, no ground, no text, no watermark. Orthographic SIDE
> view, camera exactly horizontal. Soft daylight from upper-left.
> Photorealistic industrial render, like a frame from a launch webcast
> telephoto lens.

生成后到 Photopea(免费网页版 PS)或 rembg 抠掉绿背景,**裁掉所有空白
边**(程序按图片高度等比缩放,留白会让火箭变小)。导出 PNG,高度 ≥1200px。

绿边处理:抠完图如果边缘有绿色描边,Photopea → Layer → Matting →
Defringe (2px)。

---

## 1. 整箭 stack.png(竖长构图,宽:高 ≈ 1 : 4.5)

> A two-stage super-heavy launch vehicle standing vertically, inspired by
> a stainless-steel rocket: lower stage is a long brushed stainless-steel
> cylinder with subtle vertical weld seams and four small dark grid fins
> near the top of the stage; upper stage is the same diameter with a
> smoothly curved ogive nose, matte black heat-shield tiles covering one
> side, and two small forward canard flaps. Slight cold LOX frost band
> near the bottom. NO engine flame, NO exhaust, NO launch tower, NO
> ground. The vehicle fills the full frame height. Tall narrow
> composition.

要点:
- **不要火焰**(程序会叠加会闪烁的尾焰;图里画了反而重影)。
- 不锈钢质感 + 黑瓦条是辨识度来源;不要画任何 logo / 文字 / 旗帜。

## 2. 助推器 booster.png(宽:高 ≈ 1 : 3.5)

> Only the lower booster stage of the same stainless-steel rocket:
> a long brushed stainless cylinder, four small dark grid fins unfolded
> near the top, dark engine skirt at the bottom, subtle weld seams and
> light scorch marks from reentry near the base. Flat top (no nose cone).
> NO flame, NO tower, NO ground. Fills full frame height.

## 3. 发射塔 tower.png(宽:高 ≈ 1 : 5)

> A tall dark steel launch-tower lattice structure, square cross-section,
> dense diagonal cross-bracing, a small crane cap at the very top,
> industrial gray-black steel with subtle rust streaks. **NO catch arms,
> NO mechanical arms** — just the straight vertical lattice mast. NO
> ground, NO rocket. Fills full frame height.

⚠️ 塔一定不要带臂:筷子臂是程序画的(它要开合、要接火箭)。

---

## 4. 人物不用生成

Boss Cam 里的卡通马斯克是**程序实时绘制的动画**(眨眼/视线跟踪火箭/
说话/捂脸/挥拳),不吃任何图片素材——不用为他生成肖像。
(assets/boss/ 里的照片是早期实验,已停用。)

---

## 5. 常见坑

- 透视:必须正侧视(orthographic side view),有一点仰角整个比例就不对。
- 图里自带火焰/烟 → 和程序火焰叠成重影,重新生成。
- 塔带了臂 → 筷子开合动画会画出第二副臂,重新生成。
- 留白没裁 → 火箭显示得特别小;务必 trim 到像素边缘。
- 任何真实公司 logo / 旗帜 / 文字都不要(版权 + 平台审核都麻烦)。

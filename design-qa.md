# 细纸纤维主题验收 — 2026-09-19

final result: passed

## 范围与依据

用户选定第一张 Cotton paper and ink 方案，随后明确要求同时使用 ChatGPT 网页版生成素材，与内置生图结果比较后择优。保留已完成的 Flask/Jinja/Bootstrap 页面、配色、布局和业务行为，仅更新背景材质。

选定方案：[原图](docs/ui/screenshots/2026-09-19-cotton-paper/selected-concept.png)。真实页面：`http://127.0.0.1:5093/admin/manage_departments`，临时 SQLite 合成数据，未使用正式数据库。

## A/B 选择

两者使用同一方案截图、同样的细纸纤维要求，均用 WebP quality 88 编码。实际页面均在 1440 x 1000px 视口、640px 背景尺寸、multiply 混合下比较。另用 320px 缩小平铺板观察重复边界。

| 指标 | 内置生成 A | 网页版 B |
| --- | ---: | ---: |
| 像素尺寸 | 1254 x 1254 | 1254 x 1254 |
| WebP 字节数 | 107556 | 95282 |
| 灰度标准差 | 2.70 | 2.33 |
| 左右边界平均灰度差 | 2.33 | 2.01 |
| 上下边界平均灰度差 | 3.09 | 2.44 |

**采用 B。** 并排观察时，B 的纤维更细、更均匀，孤立深色长纤维更少，浅底文字更安静；两份在检查尺寸下均未见明显平铺接缝。B 比 A 小 11.4%。像素指标仅作为低对比和边界连续性的辅助证据，选择以真实页面观感为主。

- [页面与平铺对照](docs/ui/screenshots/2026-09-19-cotton-paper/texture-comparison.html)
- [对照截图](docs/ui/screenshots/2026-09-19-cotton-paper/texture-comparison.png)
- [方案与最终页面](docs/ui/screenshots/2026-09-19-cotton-paper/comparison.html)
- [最终桌面](docs/ui/screenshots/2026-09-19-cotton-paper/final-desktop.png)
- [最终手机](docs/ui/screenshots/2026-09-19-cotton-paper/final-mobile.png)
- [工作台](docs/ui/screenshots/2026-09-19-cotton-paper/final-workspace.png)
- [登录页](docs/ui/screenshots/2026-09-19-cotton-paper/final-login.png)

## 视觉与交互验收

- 方案图为 AI 视觉参考，比较时按相同比例展示顶部区域；实现保持真实页面文字与布局，不复制生成图中的文字误差。
- 最终网页素材：390 / 768 / 960 / 1024 / 1440px 均检查文档宽度，未超过视口；系统滚动条占用的 15px 不算溢出。
- 人员卡片圆角正常，卡片正文、输入框的背景图计算值为 `none`；纹理没有覆盖层，不会截获点击。
- 部门更多菜单实际打开与关闭通过。登录页成功登录后返回工作台。
- 工作台招呼区、侧栏、页面空白和登录展示区共享纸纹；数据区、按钮、输入区保持原来样式。
- 实际读取最终素材并核对 SHA256，与网页候选 B 一致。
- 浏览器控制台未见错误；全页截图接口不稳定时使用视口截图保存证据，未将失败截图当作验收结果。
- 没有发现本次材质范围内的 P0/P1/P2 问题。纹理重复方式为静态背景，不添加动态效果或依赖。

## 回归

UI 相关回归：163 passed、352 subtests passed，无 skipped。最终替换 B 后再运行真实 Chromium 主题测试和导航交互测试：14 passed、2 subtests passed。`git diff --check` 通过。

本轮为 CSS 和静态图片改动，未重新运行前一轮的全后端测试，也未进行正式服务器部署。

## 顶栏 B 版落地 — 2026-09-19

用户明确选择 A/B 预览中的 B 版。公共顶栏改为内收圆角纸纹条：桌面 20px 圆角、顶部 12px 粘性定位；手机 16px 圆角、56px 最小高度。保留原有导航按钮、搜索、账户菜单和页面标签，正文顶部留白与预览一致。

主版本实际检查 390 / 768 / 960 / 1024 / 1440px，无文档横向溢出。桌面用户菜单可打开关闭；手机导航打开、Escape 关闭及焦点返回“打开导航”通过。相关 UI 回归：163 passed、352 subtests passed，无 skipped；`git diff --check` 通过。本轮没有后端改动或正式服务器部署。

- [主版本桌面截图](docs/ui/screenshots/2026-09-19-topbar-b/desktop.png)
- [主版本手机截图](docs/ui/screenshots/2026-09-19-topbar-b/mobile.png)

本次 scoped design QA: passed。

## 全局卡片 B 版落地 — 2026-09-19

用户明确要求全站此类白色卡片统一使用现有材质，并在六页 A/B 对照后选择 B。已将预览的通用卡片、指标、面板、提示容器、审核容器、课程/人员/统计分区、弹窗卡片与登录容器背景规则应用到主版本；选中态、输入框、按钮和状态色保持原有语义。标题区去除普通白底，保留语义色标题。

验收：主版本工作台、审核、人员、统计、课程、设置六页在 1440px 均无横向溢出；390px 人员页无溢出，输入框/选择框没有纸纹，部门菜单打开关闭正常。实际截图与已选 B 保持一致。统一后卡片与底面的明度差更小，层次依靠原有边框、留白与字号，这是 A/B 中已展示的效果。

更新真实 Chromium 回归契约：卡片应加载纸纹，输入框与主按钮仍无纹理，点击命中检查继续通过。修改前新断言失败，应用 B 后相关 UI 回归 163 passed、352 subtests passed，无 skipped；`git diff --check` 通过。CSS 静态材质变更，本轮未重复全后端回归，未部署服务器。

截图目录：`docs/ui/screenshots/2026-09-19-cards-b/`。本次 scoped design QA: passed。

## 2026-09-19 — approved detail B and mobile navigation button

User selected the detail B comparison. Applied warm inset backgrounds for neutral light panels and tables, softer nested group borders, intact course heading words, finer table separators, and clearer sort / restriction controls. Removed superseded page-local course background declarations so shared styles win.

The mobile navigation button now reveals the existing paper topbar through a transparent background with a subtle border; hover/open states use the warm inset tone. The 44px hit target and keyboard focus outline remain.

Validation: UI regression 163 passed, 352 subtests passed (37 warnings; 18.83s). Removed empty CSS rules afterward (no behavior change). Main preview inspected at 1600x1000 and 390x844; mobile navigation opens, Escape closes it and restores focus to the trigger. Local preview template cache was refreshed by restarting the preview against the same isolated synthetic database. Screenshots: `docs/ui/screenshots/2026-09-19-details-b/`. No production deployment or full backend regression in this CSS-focused round.

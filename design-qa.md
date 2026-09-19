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

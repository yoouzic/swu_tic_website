# Site Capture Implementation Plan

> **For agentic workers:** Use executing-plans to implement task-by-task in the existing checkout.

**Goal:** 保存现场照片与时间，确认课程后课后恢复草稿补完。

**Architecture:** 独立LectureSiteCapture记录与私有图片存储，受保护capture_id连接现有草稿和表单；本地OCR与作息推算独立于课表匹配。试用前端使用新采集卡与现有确认服务。

**Tech Stack:** Flask/SQLAlchemy、Pillow/RapidOCR ONNX、浏览器相机/定位。

- [x] 测试：数字识别过滤、节次边界、无定位/无匹配仍保存、草稿保护、照片访问隔离与最终关联。
- [x] 实现模型、OCR/时间服务与采集路由；新增依赖并初始化调试数据库。
- [x] 实现精简相机卡、教室修正、课程确认、课后继续入口；融合草稿恢复与清除。
- [x] 验证真实OCR管道与真实浏览器模拟相机，保留截图/逐步状态；运行相关回归。
- [x] 报告可试用范围、OCR实测证据及手机/真实门牌限制。

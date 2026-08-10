# 夸克网盘资源搜索器 - 云端部署指南

部署到云服务器后，即使电脑关机也能随时随地用手机访问。

---

## 方案一：Render.com（推荐 ⭐ 最简单）

**优点**: 免费套餐足够 24/7 运行，无需信用卡，GitHub 自动部署

### 步骤（5分钟）

1. 将 `outputs/quark_search/` 整个目录上传到 GitHub 仓库
   ```bash
   cd ~/WorkBuddy/WorkBuddy/outputs/quark_search
   git init
   git add .
   git commit -m "夸克网盘搜索器"
   git remote add origin https://github.com/你的用户名/quark-search.git
   git push -u origin main
   ```

2. 打开 [render.com](https://render.com)，用 GitHub 账号注册登录

3. 点击 **New +** → **Web Service** → 选择你的 `quark-search` 仓库

4. Render 会自动检测 `render.yaml`，所有配置已就绪：
   - Runtime: Python 3
   - Build: `pip install -r requirements.txt`
   - Start: Python uvicorn server
   - 健康检查: `/api/health`

5. 点击 **Create Web Service**，等待 2-3 分钟构建

6. 你会得到一个 `https://quark-search.onrender.com` 的永久地址

**免费套餐限制**: 每月 750 小时（刚好 24/7），15 分钟无访问自动休眠（有人访问会自动唤醒，约 30 秒）

---

## 方案二：Railway.app（备选）

**优点**: 更快的冷启动，界面友好

1. 同上，先把代码推到 GitHub

2. 打开 [railway.app](https://railway.app)，用 GitHub 登录

3. **New Project** → **Deploy from GitHub repo** → 选择 `quark-search`

4. Railway 自动检测 Python 项目，自动构建部署

5. 你会得到一个 `https://quark-search.up.railway.app` 地址

**免费套餐**: $5 信用额度，大约够用一个月

---

## 方案三：Docker 部署到任意 VPS

如果你有 VPS（阿里云/腾讯云/任意 Linux 服务器）：

```bash
# 在 VPS 上
git clone https://github.com/你的用户名/quark-search.git
cd quark-search
docker build -t quark-search .
docker run -d -p 80:8899 --name quark-search --restart always quark-search
```

然后配置 Nginx 反向代理 + SSL 证书即可。

---

## 方案四：Fly.io

```bash
# 安装 flyctl
brew install flyctl
# 部署
cd outputs/quark_search
fly launch  # 会引导你完成配置
fly deploy
```

---

## 部署后的手机使用

部署完成得到公网地址后（如 `https://quark-search.onrender.com`）：

- 📱 **iPhone Safari / 安卓 Chrome** 直接打开网址
- 🔍 输入资源名搜索
- 🔗 点击「打开夸克」自动唤起夸克APP保存资源
- ⭐ 支持添加到手机主屏幕（PWA 体验）

---

## 已配置的环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PORT` | 服务端口 | 8899（云平台会自动注入） |

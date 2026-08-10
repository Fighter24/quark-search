# 夸克网盘资源搜索器 - 部署到 GitHub 指南

## 第一步：推送到 GitHub

```bash
cd ~/WorkBuddy/WorkBuddy/outputs/quark_search

# 1. 在 GitHub 上创建新仓库：https://github.com/new
#    仓库名建议: quark-search，设为 Public

# 2. 添加远程仓库（替换为你的仓库地址）
git remote add origin https://github.com/你的用户名/quark-search.git

# 3. 推送
git push -u origin main
```

## 第二步：部署到云端（选一个）

### Render.com（推荐，免费24/7）

1. 打开 https://render.com，用 GitHub 登录
2. 点 **New +** → **Web Service**
3. 选择 `quark-search` 仓库
4. Render 自动识别 `render.yaml`，直接点 **Create Web Service**
5. 等2分钟，得到 `https://quark-search.onrender.com`
6. 📱 手机上打开这个地址就能用

### 家里的 Windows 电脑（备选）

1. 把 `outputs/quark_search/` 文件夹复制到 Windows 电脑
2. 安装 Python 3.10+：https://www.python.org/downloads/
3. 双击 `start.bat` 启动
4. 手机连接同一 WiFi，浏览器打开 `http://电脑IP:8899`
5. 如需外网访问，安装 Tailscale：https://tailscale.com/download
   - Mac和Windows都装
   - 手机上浏览器打开 `http://Windows电脑的Tailscale IP:8899`

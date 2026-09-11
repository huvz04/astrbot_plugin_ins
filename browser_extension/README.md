# AstrBot Instagram 浏览器桥接

1. 先在 AstrBot 的 `astrbot_plugin_ins` 配置中开启浏览器桥接，保存并重载插件，再复制自动生成的桥接密钥。
2. 在 AstrBot「设置 → API Keys」新建一个勾选 `plugin` 权限的 API Key。需要 AstrBot 4.26 或更高版本。
3. 打开 Chrome 的 `chrome://extensions`，开启「开发者模式」，点击「加载已解压的扩展程序」，选择本文件夹。
4. 在自动打开的设置页填写 Chrome 能访问的 AstrBot 地址、AstrBot API Key 和插件桥接密钥，点击「保存并测试」。
5. 确认 Chrome 已登录 Instagram，点击「立即扫描」。Chrome 保持运行时，扩展会按照插件设置的间隔继续扫描。

AstrBot 地址示例：`http://192.168.1.20:6185`。远程部署或 Docker 部署请填写 Chrome 所在电脑实际能访问的地址。

扩展不会读取或发送 Instagram Cookie。两个密钥保存在 Chrome 扩展本地存储中，请勿分享。

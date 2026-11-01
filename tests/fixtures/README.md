# 媒体验证样本

`library-preview.mp4` 与 `library-preview.webm` 是本地生成的 64×64 蓝色画面，时长 1 秒、5 fps，不含声音及用户资料。用于真实容器校验、Range 下载和浏览器预览验证。

生成命令（开发验证工具，不是运行时依赖）：

```powershell
ffmpeg -f lavfi -i color=c=blue:s=64x64:r=5:d=1 -c:v libx264 -pix_fmt yuv420p -movflags +faststart library-preview.mp4
ffmpeg -i library-preview.mp4 -c:v libvpx-vp9 -b:v 40k library-preview.webm
```

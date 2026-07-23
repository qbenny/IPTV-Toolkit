/**
 * clipboard — 公共剪贴板工具
 * 用法: copyToClipboard(text, showToast)
 */
window.copyToClipboard = async (text, showToast) => {
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            if (showToast) showToast('已复制到剪贴板');
            return;
        }
    } catch (e) {}
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta);
    try { ta.select(); document.execCommand('copy'); if (showToast) showToast('已复制到剪贴板'); }
    catch (e) { if (showToast) showToast('复制失败', 'error'); }
    finally { document.body.removeChild(ta); }
};

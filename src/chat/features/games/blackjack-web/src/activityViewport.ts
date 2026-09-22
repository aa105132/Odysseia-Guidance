/** Discord活动物理视口与横向逻辑坐标，不依赖设备旋转权限。 */
type ActivityViewport = { width: number; height: number; physicalWidth: number; physicalHeight: number; rail: number; rotated: boolean; offsetLeft: number; offsetTop: number };
let current: ActivityViewport = { width: 0, height: 0, physicalWidth: 0, physicalHeight: 0, rail: 0, rotated: false, offsetLeft: 0, offsetTop: 0 };

function embedded() {
  if (new URLSearchParams(location.search).has('frame_id')) return true;
  try { return window.self !== window.top; } catch { return true; }
}
export function updateActivityViewport() {
  const visual = window.visualViewport;
  const width = Math.max(1, Math.round(visual?.width ?? innerWidth));
  const height = Math.max(1, Math.round(visual?.height ?? innerHeight));
  const rail = embedded() ? 64 : 0;
  const physicalWidth = Math.max(1, width - rail);
  const touchDevice = matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0;
  const rotated = height > width && (width <= 768 || touchDevice);
  current = { width: rotated ? height : physicalWidth, height: rotated ? physicalWidth : height, physicalWidth, physicalHeight: height, rail, rotated, offsetLeft: visual?.offsetLeft ?? 0, offsetTop: visual?.offsetTop ?? 0 };
  const style = document.documentElement.style;
  style.setProperty('--activity-width', `${current.width}px`);
  style.setProperty('--activity-height', `${current.height}px`);
  style.setProperty('--activity-physical-width', `${physicalWidth}px`);
  style.setProperty('--activity-physical-height', `${height}px`);
  style.setProperty('--activity-rail', `${rail}px`);
  style.setProperty('--activity-rotation', rotated ? '90deg' : '0deg');
  style.setProperty('--activity-offset-left', `${current.offsetLeft}px`);
  style.setProperty('--activity-offset-top', `${current.offsetTop}px`);
  document.documentElement.dataset.activityRotated = String(rotated);
  document.documentElement.dataset.activityEmbedded = String(rail > 0);
  return { ...current };
}
export function clientPointToActivity(x: number, y: number) {
  const relativeX = x - current.offsetLeft;
  const relativeY = y - current.offsetTop;
  return current.rotated ? { x: relativeY, y: current.physicalWidth - relativeX } : { x: relativeX, y: relativeY };
}
export function installActivityViewport() {
  updateActivityViewport();
  window.addEventListener('resize', updateActivityViewport);
  window.visualViewport?.addEventListener('resize', updateActivityViewport);
  window.visualViewport?.addEventListener('scroll', updateActivityViewport);
  return () => {
    window.removeEventListener('resize', updateActivityViewport);
    window.visualViewport?.removeEventListener('resize', updateActivityViewport);
    window.visualViewport?.removeEventListener('scroll', updateActivityViewport);
  };
}

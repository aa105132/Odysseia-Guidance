/// <reference types="vite/client" />

/* 路由 meta 类型增强：public 标记免鉴权路由，title 供顶栏展示
 * 关键：本文件必须有顶层 export 才被视作「模块」，declare module 才是「增强(merge)」
 * 而非「环境声明(覆盖)」——否则会遮蔽真实 vue-router 导出，致 useRoute/createRouter 等全部报
 * "has no exported member"。空 export {} 即可满足条件，勿删。 */
declare module 'vue-router' {
  interface RouteMeta {
    public?: boolean;
    title?: string;
  }
}

export {};

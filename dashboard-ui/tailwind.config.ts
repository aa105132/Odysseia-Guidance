import type { Config } from 'tailwindcss';

/**
 * 设计方向B「月月的工坊台」
 * 暖中性深色 + 单一琥珀强调 + 思源宋体展示字 + 思源黑体正文
 *
 * 反 AI slop：通过 theme.colors 完整替换默认色板，移除 violet/indigo/fuchsia/cyan/
 * blue/purple/sky 等紫蓝色族，仅保留中性基础色；语义色全部走 tokens.css 的 CSS 变量。
 * 注：tailwind 的 extend 只能合并、无法删除默认 shade，故用 theme.colors 替换法
 * （即 task 所述 "purge" 路线）来真正禁用紫蓝色板。
 * 色板/字体/间距/圆角数值统一由 tokens.css 定义，此处只做变量映射，禁改动数值。
 */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    // 完整替换默认色板：只保留透明/继承/黑白/灰阶，其余默认饱和色一律移除
    colors: {
      transparent: 'transparent',
      current: 'current',
      inherit: 'inherit',
      black: '#000',
      white: '#fff',
      gray: {
        50: '#f9fafb',
        100: '#f3f4f6',
        200: '#e5e7eb',
        300: '#d1d5db',
        400: '#9ca3af',
        500: '#6b7280',
        600: '#4b5563',
        700: '#374151',
        800: '#1f2937',
        900: '#111827',
        950: '#030712',
      },
    },
    extend: {
      colors: {
        // 背景层级（暖深棕灰）
        'bg-base': 'var(--bg-base)',
        'bg-surface': 'var(--bg-surface)',
        'bg-surface-2': 'var(--bg-surface-2)',
        'bg-inset': 'var(--bg-inset)',
        // 边框
        border: 'var(--border)',
        'border-strong': 'var(--border-strong)',
        // 文本
        'text-primary': 'var(--text-primary)',
        'text-secondary': 'var(--text-secondary)',
        'text-muted': 'var(--text-muted)',
        'text-placeholder': 'var(--text-placeholder)',
        // 强调（唯一琥珀，频率压低：nav 激活/主按钮/单一灵石锚点/关键 KPI 数值）
        accent: 'var(--accent)',
        'accent-hover': 'var(--accent-hover)',
        'accent-subtle': 'var(--accent-subtle)',
        // 语义
        success: 'var(--success)',
        danger: 'var(--danger)',
        warning: 'var(--warning)',
        info: 'var(--info)',
      },
      fontFamily: {
        // 展示字（标题/数值/锚点）：思源宋体
        // 正文（表单/列表）：思源黑体
        // 实际 woff2 自托管 + @font-face 栈回退由 tokens.css 定义
        display: ['var(--font-display)'],
        sans: ['var(--font-sans)'],
      },
      spacing: {
        // 间距阶梯 4/8/12/16/24/32/48 px
        1: 'var(--space-1)',
        2: 'var(--space-2)',
        3: 'var(--space-3)',
        4: 'var(--space-4)',
        6: 'var(--space-6)',
        8: 'var(--space-8)',
        12: 'var(--space-12)',
      },
      borderRadius: {
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        // DEFAULT 指向 --radius-md（token 体系无 --radius 裸名，取中档作默认圆角）
        DEFAULT: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        xl: 'var(--radius-xl)',
      },
    },
  },
  plugins: [],
} satisfies Config;

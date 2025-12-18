## 问题分析

对比原始HTML和翻译后HTML，问题清晰可见：

**原始HTML（第78-82行）**：
```html
<div class="mystyle_padding-start_1em">
  <p>
    <span class="upright">②</span>
    フェラ男優
  </p>
</div>
```

**翻译后HTML（第94-104行）**：
```html
<div class="bilingual-container">
  <div class="mystyle_padding-start_1em" class="original-text">
    <div class="bilingual-container">
      <p class="original-text">
        <span class="upright">②</span>
        フェラ男優
      </p>
      <p class="translated-text">
        <span class="upright">②</span>
        口交男优
      </p>
    </div>
  </div>
  <div class="mystyle_padding-start_1em" class="translated-text">
    <p>
      <span class="upright">②</span>
      口交男优
    </p>
  </div>
</div>
```

## 问题根源

1. **双重处理问题**：代码同时处理了外层的 `<div class="mystyle_padding-start_1em">` 和内层的 `<p>` 标签，导致嵌套的 `bilingual-container`
2. **class属性重复**：第94行出现了重复的 `class` 属性：`class="mystyle_padding-start_1em" class="original-text"`
3. **原始块识别错误**：增量更新时，代码没有正确识别哪些块已经被处理过
4. **正则表达式缺陷**：第1365行和1370行的正则表达式在处理带有class属性的标签时，没有合并class属性，而是直接添加，导致重复

## 修复方案

### 1. 改进原始块识别
- 在增量更新时，确保每个原始块只被处理一次
- 添加检查机制，避免对同一内容进行重复处理

### 2. 修复class属性处理逻辑
- 修改第1365行和1370行的正则表达式，使其能够正确处理已有class属性的标签
- 确保class属性被合并，而不是重复添加

### 3. 添加嵌套检查
- 在处理HTML块之前，检查原始块是否已经包含 `bilingual-container` 类
- 如果已包含，则跳过处理，避免嵌套

### 4. 优化块处理顺序
- 优先处理最内层的元素，再处理外层容器
- 或者，确保父容器和子元素不会被同时作为独立块处理

## 具体修改点

1. **文件**：`epub_translator.py`
2. **函数**：`update_file_content_by_type_incremental`
3. **修改内容**：
   - 在第1361行之前添加嵌套检查：`if 'bilingual-container' in original_block: continue`
   - 改进第1365行的正则表达式，使其能够正确合并class属性
   - 改进第1370行的正则表达式，同样处理class属性合并
   - 修复重复class属性问题，确保每个标签只有一个class属性

## 预期效果

修复后，HTML结构将变为：
```html
<div class="bilingual-container">
  <div class="mystyle_padding-start_1em original-text">
    <p>
      <span class="upright">②</span>
      フェラ男優
    </p>
  </div>
  <div class="mystyle_padding-start_1em translated-text">
    <p>
      <span class="upright">②</span>
      口交男优
    </p>
  </div>
</div>
```

结构清晰，没有嵌套问题，class属性正确合并，每个翻译块只被处理一次。
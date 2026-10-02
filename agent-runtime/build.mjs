// 只打包 Pi 核心和 OpenAI 兼容协议，不引入 coding CLI、插件或 Skill 加载器。
import { build } from 'esbuild';
import { readFile, readdir, writeFile } from 'node:fs/promises';
const result = await build({
  entryPoints: ['src/runtime.mjs'], outfile: 'dist/runtime.mjs', bundle: true,
  platform: 'node', target: 'node22', format: 'esm', legalComments: 'eof',
  banner: { js: 'import { createRequire } from "node:module"; const require = createRequire(import.meta.url);' },
  metafile: true,
});
// 只归档真正进入单文件运行时的依赖许可；缺少许可证即停止发行构建。
const packages = new Set(Object.keys(result.metafile.inputs)
  .filter(path => path.startsWith('node_modules/'))
  .map(path => path.split('/').slice(0, path.split('/')[1].startsWith('@') ? 3 : 2).join('/')));
const notices = [];
for (const path of [...packages].sort()) {
  const pkg = JSON.parse(await readFile(`${path}/package.json`, 'utf8'));
  const files = (await readdir(path)).filter(file => /^(licen[cs]e|copying|notice)(\.|$)/i.test(file));
  const texts = await Promise.all(files.map(file => readFile(`${path}/${file}`, 'utf8')));
  if (!texts.length && pkg.name.startsWith('@earendil-works/pi-'))
    texts.push(await readFile('pi-LICENSE', 'utf8'));
  if (!texts.length) throw new Error(`发行依赖缺少完整许可: ${pkg.name}`);
  notices.push({ name: pkg.name, version: pkg.version, license: pkg.license, texts });
}
await writeFile('dist/third-party-notices.json', JSON.stringify(notices, null, 2) + '\n');
console.info('Pi 内置运行时构建完成');

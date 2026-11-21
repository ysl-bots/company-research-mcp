const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.COMPANY_PLAYWRIGHT_MODULE || 'playwright');

const root = process.env.COMPANY_COLLECTION_DIR || __dirname;
const checkpoint = path.join(root, 'discovery');
const output = path.join(root, 'playwright');
const retryOnly = process.argv.includes('--retry-failed');
const overviewOnly = process.argv.includes('--overview-only');
const fields = {
  company_overview: ['company_overview_url', 'homepage_url'],
  talent_profile: ['talent_url', 'careers_url'],
};

async function collect(browser, record, kind, retry = false) {
  const [primary, fallback] = kind === 'talent_profile' && record.prefer_careers
    ? ['careers_url', 'talent_url'] : fields[kind];
  const url = retry ? record[fallback] || record[primary] : record[primary] || record[fallback];
  if (!url) return {kind, status: 'url_unverified'};
  const page = await browser.newPage({locale:'ko-KR', acceptDownloads:false});
  try {
    await page.route('**/*', route => {
      const request = route.request();
      const hostname = new URL(request.url()).hostname;
      if (hostname === 'localhost' || hostname === 'metadata.google.internal' ||
          hostname.endsWith('.local') || hostname.endsWith('.internal') ||
          /^[0-9.]+$/.test(hostname) || hostname.startsWith('[')) return route.abort();
      if (['font', 'media'].includes(request.resourceType())) return route.abort();
      return route.continue();
    });
    const response = await page.goto(url, {waitUntil:'domcontentloaded',timeout:20000});
    await page.locator('main,[role="main"],#content,#contents').first().waitFor({timeout:3500}).catch(() => {});
    const data = await page.evaluate(({category, overviewOnly}) => {
      const root = document.querySelector('main,[role="main"],#content,#contents') || document.body;
      if (!root) return {title:document.title, headings:[], labels:[], images:[]};
      const relevant = category === 'company_overview'
        ? /회사소개|회사 소개|기업소개|기업 소개|기업개요|기업 개요|경영철학|사업영역|비전|about us|overview|mission|vision/i
        : /인재상|인재 철학|인재철학|핵심역량|일하는 방식|업무문화|기업문화|채용|our people|culture|values/i;
      const headings = Array.from(root.querySelectorAll('h1,h2,h3,h4'), h=>h.innerText.trim()).filter(Boolean);
      const labels = (root.innerText || '').split('\n').map(line=>line.trim())
        .filter(line=>line && line.length<=70 && relevant.test(line))
        .filter((line,index,list)=>list.indexOf(line)===index).slice(0,12);
      const snippets = Array.from(root.querySelectorAll('p,dd,li'))
        .map(node=>node.innerText.trim().replace(/\s+/g,' '))
        .filter(line=>line.length>=20 && relevant.test(line))
        .filter((line,index,list)=>list.indexOf(line)===index)
        .slice(0,3).map(line=>line.slice(0,75));
      const images = Array.from(root.querySelectorAll('img'))
        .filter(img=>relevant.test([img.alt,img.getAttribute('src')].join(' ')))
        .slice(0,8).map(img=>({alt:img.alt,url:img.currentSrc||img.src}));
      const bodyCandidates = overviewOnly
        ? Array.from(root.querySelectorAll('p, dd, article, section'))
          .map(node=>node.innerText.trim().replace(/\s+/g,' '))
          .filter(line=>line.length>=35 && line.length<=400)
          .filter((line,index,list)=>list.indexOf(line)===index)
          .sort((a,b)=>Number(relevant.test(b))-Number(relevant.test(a)))
          .slice(0,4).map(line=>line.slice(0,120))
        : undefined;
      return {title:document.title,headings:headings.slice(0,12),labels,snippets,images,bodyCandidates};
    },{category:kind,overviewOnly});
    let status = url === record[primary] && data.labels.length ? 'evidence_candidate' : 'needs_review';
    if (!response || !response.ok()) status = 'fetch_failed';
    if (new URL(url).hostname.replace(/^www\./,'') !== new URL(page.url()).hostname.replace(/^www\./,'')) status = 'needs_review';
    return {kind,url,final_url:page.url(),http_status:response?.status(),status,...data};
  } catch (error) {
    return {kind,url,status:'fetch_failed',error:String(error.message).split('\n')[0]};
  } finally {
    await page.close();
  }
}

async function main() {
  await fs.mkdir(output,{recursive:true});
  const browser = await chromium.launch({executablePath:process.env.COMPANY_CHROMIUM || undefined});
  try {
    for (;;) {
      const done = new Set(await fs.readdir(output));
      const source = (await fs.readdir(checkpoint)).filter(name=>/^\d{8}\.json$/.test(name));
      const pending = source.filter(name=>retryOnly ? done.has(name) : !done.has(name));
      if (!pending.length) {
        if (retryOnly) break;
        if (await fs.stat(path.join(root,'discovery.finished')).then(()=>true,()=>false)) break;
        await new Promise(resolve=>setTimeout(resolve,12000));
        continue;
      }
      let next = 0;
      async function worker() {
        while (next < pending.length) {
          const name = pending[next++];
          const record = JSON.parse(await fs.readFile(path.join(checkpoint,name),'utf8'));
          let pages;
          let retried = false;
          if (retryOnly) {
            const previous = JSON.parse(await fs.readFile(path.join(output,name),'utf8'));
            if (previous.retried || !previous.pages.some(page=>page.status==='fetch_failed')) continue;
            pages = await Promise.all(previous.pages.map(async page=>{
              if (page.status !== 'fetch_failed') return page;
              const result = await collect(browser,record,page.kind,true);
              return {...result,first_attempt:page};
            }));
            retried = true;
          } else {
            const kinds = overviewOnly ? ['company_overview'] : Object.keys(fields);
            pages = await Promise.all(kinds.map(kind=>collect(browser,record,kind)));
          }
          const temporary = path.join(output,name+'.tmp');
          await fs.writeFile(temporary,JSON.stringify({...record, method:'playwright', retried, checked_at:new Date().toISOString(),pages},null,2)+'\n');
          await fs.rename(temporary,path.join(output,name));
          console.log(record.corp_code, ...pages.map(page=>page.status));
        }
      }
      await Promise.all(Array.from({length:retryOnly ? 6 : overviewOnly ? 18 : 12},()=>worker()));
      if (retryOnly) break;
    }
  } finally {
    await browser.close();
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});

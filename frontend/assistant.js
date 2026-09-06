'use strict';
let assistantDataset, assistantDraft=null;
function renderAssistant(){
 const host=document.querySelector('#assistant');
 if(!host.firstElementChild){
  host.innerHTML=`<div class="assistant-heading"><h2>✦ 排产助手</h2><span class="assistant-mode">本地规则模式 · LLM 未接入</span></div><p>先检查数据，再明确排产目标。助手帮助理解约束、准备配置与分析结果。</p><div class="assistant-actions"><button data-assist="check">检查数据风险</button><button data-assist="strategy">如何选择方案？</button><button data-assist="explain">分析未排原因</button></div><form class="assistant-form"><input id="assistant-input" maxlength="300" aria-label="输入排产需求" placeholder="试试：为 WF 工站生成均衡优化方案"><button class="primary" type="submit">识别排产需求 →</button></form><p class="draft-help">把一句话转换成“排产范围”和“方案偏好”。确认后填入下方表单，再由您点击生成方案；也可以直接手动选择。</p><div class="assistant-answer" role="status"></div><details><summary>助手的能力与使用边界</summary><p>当前按固定规则识别工站与“快速／均衡”偏好，分析仅引用当前导入数据和已生成结果。不支持交期、插单、停机等自由约束修改；这些需求不会被静默应用。配置需由您确认后，再点击“生成排产方案”。LLM 后续可用于需求澄清与解释，设备资格、占用冲突等硬约束仍由排产引擎核验。</p></details><details><summary>接入 LLM 后，可以帮我做什么？</summary><ul class="llm-scenarios"><li><strong>理解需求与追问：</strong>听懂更自然的描述，缺少工站、目标等信息时主动澄清，再生成待确认的设置。</li><li><strong>解释数据与结果：</strong>结合真实批次、设备和任务记录，回答“为什么这批没排上”“哪些设备受限”，并给出依据。</li><li><strong>比较方案：</strong>接入方案比较工具后，解释同一数据集下不同方案的等待时间、设备负荷等差异。</li><li><strong>协助异常重排：</strong>插单、停机、交期调整需要先扩展算法约束与业务接口，LLM 再协助分析影响和提出重排建议。</li></ul><p>以上为后续能力规划。接入模型接口本身不会自动获得这些业务能力；最终安排仍由排产引擎计算、调度员确认。</p></details>`;
  host.querySelectorAll('[data-assist]').forEach(b=>b.onclick=()=>assistantAnswer(b.dataset.assist));
  host.querySelector('form').onsubmit=e=>{e.preventDefault();assistantAnswer('draft');};
 }
 if(assistantDataset!==S.dataset?.id){assistantDataset=S.dataset?.id;assistantDraft=null;host.querySelector('.assistant-answer').textContent='';}
}
function assistantAnswer(action){
 const host=document.querySelector('.assistant-answer'),d=S.dataset;
 assistantDraft=null;
 if(action==='strategy'){host.textContent='快速生成：按优先级、可选设备数量与加工时间安排任务，适合先获得可行方案。\n均衡优化：在同优先级内搜索任务顺序，尝试改善等待和设备负荷；不保证一定优于快速方案。\n两种策略都保留在制占用、设备资格和维修窗口约束。建议使用同一数据集、同一工站分别生成并比较结果。';return;}
 if(!d){host.textContent='请先上传并校验生产数据，助手才能结合实际工站与批次给出建议。';return;}
 if(action==='check'){host.textContent=`依据：${d.filename}，快照 ${d.snapshot}\n当前有 ${num(d.counts.pending)} 批待排任务、${num(d.counts.machines)} 台设备。\n${d.warnings.length?d.warnings.map((w,i)=>`${i+1}. ${w}`).join('\n'):'未发现格式或关键字段提示。'}\n下一步：核对数据提示以及设备台账，再选择工站生成方案。格式通过不等于已核实现场状态。`;return;}
 if(action==='explain'){
  if(!S.result){host.textContent='请先生成或打开一份已完成方案，才能分析该方案的未排原因。';return;}
  const counts={};for(const row of S.result.unscheduled||[]){const reason=row['未排原因']||'未提供原因';counts[reason]=(counts[reason]||0)+1;}
  host.textContent=`依据：任务 ${S.job.id.slice(0,8)} · ${S.job.station||'全部工站'}\n已安排 ${num(S.result.summary.scheduled)} 批，未安排 ${num(S.result.summary.unscheduled)} 批。\n${Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([r,n])=>`${r}：${n} 批`).join('\n')||'该范围没有未排批次。'}\n${Object.keys(counts).length?'建议在“排产方案 → 未排原因”核对具体批次，修正对应源数据后重新导入排产。':'下载方案后，请结合现场最新状态复核再执行。'}`;return;
 }
 const request=document.querySelector('#assistant-input').value.trim();
 if(!request){host.textContent='请输入工站和方案偏好，例如“WF 工站快速生成”或“全部工站均衡优化”。';return;}
 const found=(d.stations||[]).filter(s=>request.toUpperCase().split(/[^A-Z0-9_-]+/).includes(s.toUpperCase()));
 const all=request.includes('全部工站');
 const quick=/快速/.test(request),balanced=/均衡/.test(request);
 const residue=request.replace(/全部工站|工站|生成|方案|排产|快速|均衡|优化|请|为|使用|采用|进行|的|我|帮|做|安排|生产/g,'').replace(/[\s，。！？,:：!?.]/g,'');
 const stationResidue=found.reduce((s,n)=>s.replace(new RegExp(n.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'gi'),''),residue);
 if(found.length>1||(all&&found.length)||(!all&&found.length!==1)||quick===balanced||stationResidue){host.textContent='当前仅支持一个工站（或全部工站）和一种偏好。请使用“WF 工站快速生成”这样的明确描述。交期、插单、停机及其他附加约束暂不支持，未更改任何配置。';return;}
 assistantDraft={station:all?'':found[0],strategy:balanced?'balanced':'quick',dataset:d.id};
 host.textContent=`待确认的排产设置\n数据集：${d.filename}\n排产范围：${assistantDraft.station||'全部工站'}\n方案偏好：${balanced?'均衡优化':'快速生成'}\n应用后请核对工作台配置，再手动生成方案。`;
 const button=document.createElement('button');button.className='secondary';button.textContent='确认并填入排产设置';
 button.onclick=()=>{if(!assistantDraft||assistantDraft.dataset!==S.dataset?.id)return;S.station=assistantDraft.station;S.strategy=assistantDraft.strategy;S.view='workspace';render();document.querySelector('#run-station').scrollIntoView({block:'center',behavior:'smooth'});toast('已应用配置，请核对后生成排产方案');};host.append(document.createElement('br'),button);
}

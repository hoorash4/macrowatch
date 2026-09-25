(() => {
  'use strict';
  class PivotLineRenderer {
    constructor(view){this.view=view;}
    draw(target){target.useBitmapCoordinateSpace(scope=>{const x=this.view.x;if(x===null)return;const h=scope.horizontalPixelRatio,px=Math.round(x*h);if(px<0||px>scope.bitmapSize.width)return;const ctx=scope.context;ctx.save();ctx.strokeStyle=this.view.color;ctx.lineWidth=Math.max(1,h);ctx.setLineDash([3*h,3*h]);ctx.beginPath();ctx.moveTo(px,0);ctx.lineTo(px,scope.bitmapSize.height);ctx.stroke();ctx.restore();});}
  }
  class PivotLineView {
    constructor(chart,time,color,timelineRows=[]){this.chart=chart;this.time=time;this.color=color;this.timelineRows=timelineRows;this.x=null;this.rendererInstance=new PivotLineRenderer(this);}
    update(){
      let coord=this.chart.timeScale().timeToCoordinate(this.time);
      if(coord===null&&Array.isArray(this.timelineRows)&&this.timelineRows.length){
        const targetMs=Date.parse(this.time);
        if(Number.isFinite(targetMs)){
          let closest=null,minDiff=Infinity;
          for(let i=0;i<this.timelineRows.length;i++){
            const rowMs=Date.parse(this.timelineRows[i]?.time);
            if(!Number.isFinite(rowMs))continue;
            const diff=Math.abs(rowMs-targetMs);
            if(diff<minDiff){minDiff=diff;closest=this.timelineRows[i].time;}
          }
          if(closest&&minDiff<=7*86400000)coord=this.chart.timeScale().timeToCoordinate(closest);
        }
      }
      this.x=coord;
    }
    renderer(){return this.rendererInstance;}
    zOrder(){return 'top';}
  }
  class PivotTimeAxisRenderer {
    constructor(view){this.view=view;}
    draw(target){target.useBitmapCoordinateSpace(scope=>{
      const x=this.view.paneView.x;
      if(x===null){this.view.bounds=null;return;}
      const h=scope.horizontalPixelRatio,v=scope.verticalPixelRatio,px=Math.round(x*h);
      if(px<0||px>scope.bitmapSize.width){this.view.bounds=null;return;}
      const ctx=scope.context,labelHeight=18*v,labelGap=2*v,labelTop=scope.bitmapSize.height-(this.view.lane+1)*labelHeight-this.view.lane*labelGap-3*v,padX=5*h;
      ctx.save();ctx.font=`${10*v}px Pretendard, sans-serif`;ctx.textBaseline='middle';
      const labelWidth=ctx.measureText(this.view.date).width+padX*2,labelX=Math.max(2*h,Math.min(px-labelWidth/2,scope.bitmapSize.width-labelWidth-2*h));
      this.view.bounds={left:labelX/h,right:(labelX+labelWidth)/h,top:labelTop/v,bottom:(labelTop+labelHeight)/v};
      ctx.strokeStyle=this.view.color;ctx.lineWidth=Math.max(1,h);ctx.setLineDash([3*h,3*h]);ctx.beginPath();ctx.moveTo(px,0);ctx.lineTo(px,labelTop);ctx.stroke();ctx.setLineDash([]);
      ctx.fillStyle=this.view.color;ctx.fillRect(labelX,labelTop,labelWidth,labelHeight);ctx.fillStyle=this.view.textColor;ctx.fillText(this.view.date,labelX+padX,labelTop+labelHeight/2);ctx.restore();
    });}
  }
  class PivotTimeAxisPaneView {
    constructor(paneView,date,color,textColor,lane=0){this.paneView=paneView;this.date=date;this.color=color;this.textColor=textColor;this.lane=lane;this.bounds=null;this.rendererInstance=new PivotTimeAxisRenderer(this);}
    renderer(){return this.rendererInstance;}
    zOrder(){return 'top';}
  }
  class PivotLinePrimitive {
    constructor(chart,time,color,textColor='#fff',lane=0,timelineRows=[]){this.view=new PivotLineView(chart,time,color,timelineRows);this.timeAxisPaneView=new PivotTimeAxisPaneView(this.view,time,color,textColor,lane);}
    updateAllViews(){this.view.update();}
    paneViews(){return [this.view];}
    timeAxisPaneViews(){return [this.timeAxisPaneView];}
  }
  function create(host) {
    let chart = null;
    let series = null;
    let data = [];
    const indicatorSeries = new Map();
    let crosshairOverlay = null;
    let crosshairValues = null;
    const crosshairMarkers = new Map();
    let indicatorPointClick = null;
    const indicatorColor='#c026d3';
    const lineColor = () => getComputedStyle(host).getPropertyValue('--historical-chart-line').trim();
    const updateLine = () => series?.applyOptions({ color: lineColor() });
    const referenceColor=type=>getComputedStyle(host).getPropertyValue(`--historical-${type.toLowerCase()}-color`).trim();
    const referenceOnlyStyle=()=>({color:getComputedStyle(host).getPropertyValue('--historical-near-miss-color').trim()||'#cbd5e1',textColor:getComputedStyle(host).getPropertyValue('--historical-near-miss-text').trim()||'#475569'});
    const nearMissStyle=()=>({color:getComputedStyle(host).getPropertyValue('--historical-quasi-core-color').trim()||'#5a92bb',textColor:'#fff'});
    const verifiedStyle=()=>({color:getComputedStyle(host).getPropertyValue('--historical-verified-color').trim()||(document.documentElement.dataset.theme==='dark'?'#475569':'#64748b'),textColor:'#fff'});
    const pivotStyle=(result,color)=>result.markerStatus==='verified'?verifiedStyle():['near_miss','overridden_key','manual_standard'].includes(result.markerStatus)?nearMissStyle():result.markerStatus==='reference_only'?referenceOnlyStyle():{color,textColor:'#fff'};
    const displayValue=value=>window.MacroWatchFrontend.formatDisplayNumber(value);
    function isoDate(time) {
      if (typeof time==='string') return time.slice(0,10);
      if (typeof time==='number' && Number.isFinite(time)) {
        return new Date(time*1000).toISOString().slice(0,10);
      }
      if (time && Number.isInteger(time.year) && Number.isInteger(time.month) && Number.isInteger(time.day)) {
        return `${time.year}-${String(time.month).padStart(2,'0')}-${String(time.day).padStart(2,'0')}`;
      }
      return '';
    }
    function pointValueAt(rows, time, key='value', chartX=null) {
      const target=isoDate(time);
      if (!target || !rows.length || target<rows[0].time || target>rows.at(-1).time) return null;
      let left=0, right=rows.length-1;
      while (left<=right) {
        const middle=Math.floor((left+right)/2), point=rows[middle];
        if (point.time===target) return point[key];
        if (point.time<target) left=middle+1; else right=middle-1;
      }
      const before=rows[right], after=rows[left];
      if (!before || !after) return null;
      const from=chartX===null?Date.parse(before.time):chart.timeScale().timeToCoordinate(before.time);
      const to=chartX===null?Date.parse(after.time):chart.timeScale().timeToCoordinate(after.time);
      const at=chartX===null?Date.parse(target):chartX;
      if (!Number.isFinite(from)||!Number.isFinite(to)||!Number.isFinite(at)||to===from) return null;
      return before[key]+(after[key]-before[key])*(at-from)/(to-from);
    }
    function ensureCrosshairOverlay() {
      if (crosshairOverlay) return;
      crosshairOverlay=document.createElement('div');
      crosshairOverlay.className='historical-crosshair-overlay';
      crosshairValues=document.createElement('div');
      crosshairValues.className='historical-crosshair-values';
      crosshairOverlay.append(crosshairValues);
      host.append(crosshairOverlay);
    }
    function hideCrosshairOverlay() {
      if (!crosshairOverlay) return;
      crosshairOverlay.hidden=true;
      for (const marker of crosshairMarkers.values()) marker.remove();
      crosshairMarkers.clear();
      crosshairValues.replaceChildren();
    }
    function markerFor(code, color) {
      let marker=crosshairMarkers.get(code);
      if (marker) return marker;
      marker=document.createElement('i');
      marker.className='historical-crosshair-marker';
      marker.style.setProperty('--crosshair-marker-color',color);
      crosshairOverlay.append(marker);
      crosshairMarkers.set(code,marker);
      return marker;
    }
    function updateCrosshair(param) {
      if (!param?.point || !param.time || !series || !data.length) return hideCrosshairOverlay();
      const indexValue=pointValueAt(data,param.time);
      if (!Number.isFinite(indexValue)) return hideCrosshairOverlay();
      const chartX=chart.timeScale().timeToCoordinate(param.time);
      if (chartX===null) return hideCrosshairOverlay();
      ensureCrosshairOverlay();
      const x=Math.round(chart.priceScale('left').width()+chartX), indicators=[];
      for (const [code,item] of indicatorSeries) {
        const plotValue=pointValueAt(item.rows,param.time,'value',chartX);
        const value=pointValueAt(item.rows,param.time,'rawValue',chartX);
        if (Number.isFinite(plotValue)&&Number.isFinite(value)) indicators.push({code,value,plotValue,color:item.color,series:item.series});
      }
      const entries=[{code:'index',value:indexValue,color:lineColor(),series},...indicators];
      crosshairValues.replaceChildren(...entries.map(entry=>{
        const value=document.createElement('span');
        value.className='historical-crosshair-value';
        value.style.backgroundColor=entry.color;
        value.textContent=displayValue(entry.value);
        return value;
      }));
      crosshairOverlay.hidden=false;
      const badges=crosshairValues.children;
      badges[0].style.left=indicatorSeries.size===0
        ? `${Math.max(2,Math.min(x-badges[0].offsetWidth/2,host.clientWidth-badges[0].offsetWidth-2))}px`
        : `${Math.max(2,x-badges[0].offsetWidth-3)}px`;
      for (let index=1;index<badges.length;index++) {
        badges[index].style.left=`${Math.min(x+3,host.clientWidth-badges[index].offsetWidth-2)}px`;
      }
      const active=new Set(indicators.map(entry=>entry.code));
      for (const [code,marker] of crosshairMarkers) {
        if (active.has(code)) continue;
        marker.remove();
        crosshairMarkers.delete(code);
      }
      for (const entry of indicators) {
        const y=entry.series.priceToCoordinate(entry.plotValue);
        if (y===null) continue;
        const marker=markerFor(entry.code,entry.color);
        marker.style.left=`${x}px`;
        marker.style.top=`${Math.round(y)}px`;
        marker.dataset.date=isoDate(param.time);
      }
    }
    function onChartClick(param) {
      if (!indicatorPointClick || !param?.point || !param.time || !chart) return;
      const date=isoDate(param.time),x=chart.priceScale('left').width()+param.point.x;
      for (const [code,item] of indicatorSeries) {
        const marker=crosshairMarkers.get(code);
        if (!marker || marker.dataset.date!==date) continue;
        if (Math.hypot(x-Number.parseFloat(marker.style.left),
          param.point.y-Number.parseFloat(marker.style.top))>6) continue;
        const value=pointValueAt(item.rows,param.time,'rawValue',param.point.x);
        if (Number.isFinite(value)) indicatorPointClick({code,date,value});
        break;
      }
    }
    function onTimeAxisClick(event) {
      if (!indicatorPointClick || !chart || event.button!==0) return;
      const rect=host.getBoundingClientRect(),timeScale=chart.timeScale();
      const x=event.clientX-rect.left-chart.priceScale('left').width();
      const y=event.clientY-rect.top-(host.clientHeight-timeScale.height());
      if(y<0||y>timeScale.height())return;
      for(const [code,item] of indicatorSeries){
        for(const primitive of [...item.primitives].reverse()){
          const bounds=primitive.timeAxisPaneView.bounds;
          if(!bounds||x<bounds.left||x>bounds.right||y<bounds.top||y>bounds.bottom)continue;
          const date=primitive.view.time,value=pointValueAt(item.rows,date,'rawValue',timeScale.timeToCoordinate(date));
          if(Number.isFinite(value))indicatorPointClick({code,date,value});
          return;
        }
      }
    }
    function ensure() {
      if (chart) return;
      if (!window.LightweightCharts) throw new Error('차트 라이브러리를 불러오지 못했습니다.');
      chart = window.LightweightCharts.createChart(host, {
        autoSize: true,
        layout: { fontFamily: 'Pretendard, system-ui, sans-serif', fontSize: 11, attributionLogo: false },
        localization: { locale: 'ko-KR', dateFormat: 'yyyy. MM. dd.' },
        rightPriceScale: { scaleMargins: { top: .08, bottom: .08 } },
        leftPriceScale: { visible: true, scaleMargins: { top: .08, bottom: .08 }, borderVisible: false, minimumWidth: 34 },
        timeScale: { timeVisible: false, secondsVisible: false, rightOffset: 8, minBarSpacing: .01, minimumHeight: 46 },
        crosshair: { mode: window.LightweightCharts.CrosshairMode.Normal,
          horzLine: { labelVisible: false } },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { axisPressedMouseMove: { time: false, price: false }, mouseWheel: true, pinch: true },
      });
      series = chart.addLineSeries({ color: lineColor(), lineWidth: 2, priceLineVisible: false,
        lastValueVisible: true, priceFormat: { type: 'custom', minMove: .01,
          formatter: value => window.MacroWatchFrontend.formatDisplayNumber(value) } });
      ensureCrosshairOverlay();
      chart.subscribeCrosshairMove(updateCrosshair);
      chart.subscribeClick(onChartClick);
      host.addEventListener('click',onTimeAxisClick,true);
      window.addEventListener('macrowatch:themechange', updateLine);
    }
    return Object.freeze({
      setData(rows) {
        data = rows;
        if (!rows.length) { series?.setData([]); series?.setMarkers([]); return; }
        ensure();
        series.setData(rows);
        chart.timeScale().fitContent();
      },
      setCycle(points) {
        if (!series) return;
        const style = {
          START: { position: 'belowBar', shape: 'arrowUp', color: referenceColor('START') },
          PEAK: { position: 'aboveBar', shape: 'arrowDown', color: referenceColor('PEAK') },
          TROUGH: { position: 'belowBar', shape: 'arrowUp', color: referenceColor('TROUGH') },
        };
        series.setMarkers(points.map(point => ({ time: point.row.time, ...style[point.type],
          text: `${point.type} · ${point.date} · ${window.MacroWatchFrontend.formatDisplayNumber(point.row.value)}` })));
      },
      setIndicators(items) {
        if(!chart&&items.length)ensure();
        const visibleRange=chart?.timeScale().getVisibleRange();
        for(const entry of indicatorSeries.values()){for(const primitive of entry.primitives)entry.series.detachPrimitive(primitive);chart?.removeSeries(entry.series);}
        indicatorSeries.clear();
        hideCrosshairOverlay();
        items.forEach(item=>{
          const color=indicatorColor,line=chart.addLineSeries({priceScaleId:'left',color,lineWidth:3,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,title:'',priceFormat:{type:'custom',minMove:.1,formatter:value=>`${Math.round(value)}`}});
          line.setData(item.displayRows);
          const pivotPriority=result=>result.markerStatus==='reference_only'?0:result.markerStatus==='verified'?1:['near_miss','overridden_key','manual_standard'].includes(result.markerStatus)?2:3, orderedPivots=[...(item.displayPivots||item.results)].sort((a,b)=>pivotPriority(a)-pivotPriority(b)), primitives=orderedPivots.map(result=>{const style=pivotStyle(result,color);return new PivotLinePrimitive(chart,result.pivotDate,style.color,style.textColor,0,data);});
          for(const primitive of primitives)line.attachPrimitive(primitive);
          indicatorSeries.set(item.meta.code,{series:line,color,primitives,rows:item.displayRows});
        });
        if(visibleRange)chart.timeScale().setVisibleRange(visibleRange);
      },
      indicatorColors(){return new Map([...indicatorSeries].map(([code,item])=>[code,item.color]));},
      setIndicatorPointClick(handler){indicatorPointClick=typeof handler==='function'?handler:null;},
      focus(from, to, markerInset = .12) {
        if (!chart || !from || !to || !data.length) return;
        const startIndex = data.findIndex(row => row.time >= from);
        let endIndex = data.findLastIndex(row => row.time <= to);
        if (startIndex < 0 || endIndex < startIndex) return;
        const span = Math.max(1, endIndex - startIndex);
        const context = Math.max(1, span * markerInset / (1 - markerInset * 2));
        chart.timeScale().setVisibleRange({
          from: data[Math.floor(Math.max(0, startIndex - context))].time,
          to: data[Math.ceil(Math.min(data.length - 1, endIndex + context))].time,
        });
      },
      fit() { chart?.timeScale().fitContent(); },
      destroy() {
        window.removeEventListener('macrowatch:themechange', updateLine);
        chart?.unsubscribeCrosshairMove(updateCrosshair);
        chart?.unsubscribeClick(onChartClick);
        host.removeEventListener('click',onTimeAxisClick,true);
        chart?.remove();
        chart = null;
        series = null;
        indicatorSeries.clear();
        crosshairOverlay?.remove();
        crosshairOverlay=null;
        crosshairValues=null;
        crosshairMarkers.clear();
        indicatorPointClick=null;
        data = [];
      },
    });
  }
  window.MacroWatchHistoricalChart = Object.freeze({ create });
})();

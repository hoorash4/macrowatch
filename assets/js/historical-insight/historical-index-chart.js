(() => {
  'use strict';
  class PivotLineRenderer {
    constructor(view){this.view=view;}
    draw(target){target.useBitmapCoordinateSpace(scope=>{const x=this.view.x;if(x===null)return;const ctx=scope.context,h=scope.horizontalPixelRatio,v=scope.verticalPixelRatio,px=Math.round(x*h),top=(8+(this.view.row%3)*18)*v;ctx.save();ctx.strokeStyle=this.view.color;ctx.lineWidth=Math.max(1,h);ctx.setLineDash([3*h,3*h]);ctx.beginPath();ctx.moveTo(px,0);ctx.lineTo(px,scope.bitmapSize.height);ctx.stroke();ctx.setLineDash([]);ctx.font=`${8*v}px Pretendard, sans-serif`;ctx.textBaseline='middle';const padX=4*h,labelHeight=14*v,textWidth=ctx.measureText(this.view.label).width,labelWidth=textWidth+padX*2,labelX=Math.min(px+4*h,scope.bitmapSize.width-labelWidth-2*h);ctx.fillStyle=this.view.color;ctx.fillRect(labelX,top,labelWidth,labelHeight);ctx.fillStyle='#fff';ctx.fillText(this.view.label,labelX+padX,top+labelHeight/2);ctx.restore();});}
  }
  class PivotLineView {
    constructor(chart,time,color,label,row){this.chart=chart;this.time=time;this.color=color;this.label=label;this.row=row;this.x=null;this.rendererInstance=new PivotLineRenderer(this);}
    update(){this.x=this.chart.timeScale().timeToCoordinate(this.time);}
    renderer(){return this.rendererInstance;}
    zOrder(){return 'top';}
  }
  class PivotLinePrimitive {
    constructor(chart,time,color,label,row){this.view=new PivotLineView(chart,time,color,label,row);}
    updateAllViews(){this.view.update();}
    paneViews(){return [this.view];}
  }
  // 공통 frontend-core가 테마/등록/해제를, 차트 라이브러리가 크기 관찰을 담당합니다.
  function create(host) {
    let chart = null;
    let series = null;
    let data = [];
    const indicatorSeries = new Map();
    const indicatorColor='#c026d3';
    const lineColor = () => getComputedStyle(host).getPropertyValue('--historical-chart-line').trim();
    const updateLine = () => series?.applyOptions({ color: lineColor() });
    const referenceColor=type=>getComputedStyle(host).getPropertyValue(`--historical-${type.toLowerCase()}-color`).trim();
    const timingText=result=>result.offsetDays===0?'기준점 당일':`기준점 ${Math.abs(result.offsetDays)}일 ${result.offsetDays<0?'전':'후'}`;
    const pivotText=result=>result.markerStatus==='candidate'?'CANDIDATE 피봇 후보':result.markerStatus==='watch'?'WATCH 조정 감시':result.referenceType==='CURRENT'?'CONFIRMED 최근 피봇':`${result.referenceType} ${timingText(result)}`;
    function ensure() {
      if (chart) return;
      if (!window.LightweightCharts) throw new Error('차트 라이브러리를 불러오지 못했습니다.');
      chart = window.LightweightCharts.createChart(host, {
        autoSize: true,
        layout: { fontFamily: 'Pretendard, system-ui, sans-serif', fontSize: 11, attributionLogo: false },
        localization: { locale: 'ko-KR', dateFormat: 'yyyy. MM. dd.' },
        rightPriceScale: { scaleMargins: { top: .08, bottom: .08 } },
        leftPriceScale: { visible: true, scaleMargins: { top: .08, bottom: .08 }, borderVisible: false, minimumWidth: 34 },
        timeScale: { timeVisible: false, secondsVisible: false, rightOffset: 8, minBarSpacing: .01 },
        crosshair: { mode: window.LightweightCharts.CrosshairMode.Normal },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      });
      series = chart.addLineSeries({ color: lineColor(), lineWidth: 2, priceLineVisible: false,
        lastValueVisible: true, priceFormat: { type: 'custom', minMove: .01,
          formatter: value => window.MacroWatchFrontend.formatDisplayNumber(value) } });
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
        // 지표마다 다른 관측일이 시간축에 추가되므로 논리 인덱스가 달라질 수 있습니다.
        // 사용자가 보고 있던 실제 날짜 범위를 보존해 지표 선택 시 축이 압축되거나 밀리지 않게 합니다.
        const visibleRange=chart?.timeScale().getVisibleRange();
        for(const entry of indicatorSeries.values()){for(const primitive of entry.primitives)entry.series.detachPrimitive(primitive);chart?.removeSeries(entry.series);}
        indicatorSeries.clear();
        items.forEach(item=>{
          const color=indicatorColor,line=chart.addLineSeries({priceScaleId:'left',color,lineWidth:3,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,title:item.meta.title,priceFormat:{type:'custom',minMove:.1,formatter:value=>`${Math.round(value)}`}});
          line.setData(item.displayRows);
          const primitives=(item.displayPivots||item.results).map((result,index)=>new PivotLinePrimitive(chart,result.pivotDate,color,pivotText(result),index));
          for(const primitive of primitives)line.attachPrimitive(primitive);
          indicatorSeries.set(item.meta.code,{series:line,color,primitives});
        });
        if(visibleRange)chart.timeScale().setVisibleRange(visibleRange);
      },
      indicatorColors(){return new Map([...indicatorSeries].map(([code,item])=>[code,item.color]));},
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
        chart?.remove();
        chart = null;
        series = null;
        indicatorSeries.clear();
        data = [];
      },
    });
  }
  window.MacroWatchHistoricalChart = Object.freeze({ create });
})();

(() => {
  'use strict';
  // 공통 frontend-core가 테마/등록/해제를, 차트 라이브러리가 크기 관찰을 담당합니다.
  function create(host) {
    let chart = null;
    let series = null;
    let data = [];
    let pivotLayer = null;
    let pivotDefinitions = [];
    const indicatorSeries = new Map();
    const indicatorColors=['#2563eb','#d97706','#059669','#7c3aed','#db2777','#0891b2','#65a30d','#ea580c'];
    const lineColor = () => getComputedStyle(host).getPropertyValue('--historical-chart-line').trim();
    const updateLine = () => series?.applyOptions({ color: lineColor() });
    const timingText=result=>result.timingType==='coincident'?'동행':`${result.leadDays}일 선행`;
    function renderPivotLines(){
      if(!pivotLayer||!chart)return;
      pivotLayer.replaceChildren();
      for(const [index,item] of pivotDefinitions.entries()){
        const x=chart.timeScale().timeToCoordinate(item.time);if(x==null)continue;
        const line=document.createElement('div'),label=document.createElement('span');
        line.className='historical-indicator-pivot-line';line.style.left=`${x}px`;line.style.setProperty('--indicator-color',item.color);label.style.top=`${8+(index%3)*18}px`;label.textContent=item.label;line.append(label);pivotLayer.append(line);
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
        leftPriceScale: { visible: false, scaleMargins: { top: .08, bottom: .08 }, borderVisible: false },
        timeScale: { timeVisible: false, secondsVisible: false, rightOffset: 8, minBarSpacing: .01 },
        crosshair: { mode: window.LightweightCharts.CrosshairMode.Normal },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      });
      series = chart.addLineSeries({ color: lineColor(), lineWidth: 2, priceLineVisible: false,
        lastValueVisible: true, priceFormat: { type: 'custom', minMove: .01,
          formatter: value => window.MacroWatchFrontend.formatDisplayNumber(value) } });
      pivotLayer=document.createElement('div');pivotLayer.className='historical-indicator-pivots';host.append(pivotLayer);
      chart.timeScale().subscribeVisibleLogicalRangeChange(renderPivotLines);
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
          START: { position: 'belowBar', shape: 'arrowUp', color: '#15803d' },
          PEAK: { position: 'aboveBar', shape: 'arrowDown', color: '#b91c1c' },
          TROUGH: { position: 'belowBar', shape: 'arrowUp', color: '#2563eb' },
        };
        series.setMarkers(points.map(point => ({ time: point.row.time, ...style[point.type],
          text: `${point.type} · ${point.date} · ${window.MacroWatchFrontend.formatDisplayNumber(point.row.value)}` })));
      },
      setIndicators(items,activeCode) {
        if(!chart&&items.length)ensure();
        // 지표마다 다른 관측일이 시간축에 추가되므로 논리 인덱스가 달라질 수 있습니다.
        // 사용자가 보고 있던 실제 날짜 범위를 보존해 지표 선택 시 축이 압축되거나 밀리지 않게 합니다.
        const visibleRange=chart?.timeScale().getVisibleRange();
        for(const entry of indicatorSeries.values())chart?.removeSeries(entry.series);
        indicatorSeries.clear();
        pivotDefinitions=[];
        items.forEach((item,index)=>{
          const color=indicatorColors[index%indicatorColors.length];
          const line=chart.addLineSeries({priceScaleId:'left',color,lineWidth:item.meta.code===activeCode?3:2,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,title:item.meta.title,priceFormat:{type:'custom',minMove:.1,formatter:value=>`${Math.round(value)}`}});
          line.setData(item.displayRows);
          for(const result of item.results.filter(result=>result.timingType!=='lagging'))pivotDefinitions.push({time:result.pivotDate,color,label:`${result.referenceType==='CURRENT'?'최근':result.referenceType} ${timingText(result)}`});
          indicatorSeries.set(item.meta.code,{series:line,color});
        });
        if(visibleRange)chart.timeScale().setVisibleRange(visibleRange);
        renderPivotLines();
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
        pivotLayer = null;
        pivotDefinitions = [];
        indicatorSeries.clear();
        data = [];
      },
    });
  }
  window.MacroWatchHistoricalChart = Object.freeze({ create });
})();

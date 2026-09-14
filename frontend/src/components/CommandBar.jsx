import SearchBox from './SearchBox'

/** The bar carries identity, the thing you do first, and the size of the
 *  world you are doing it to.
 *
 *  Every figure here is counted from the loaded extract. Nothing is a
 *  placeholder: before the network arrives the readouts are absent rather than
 *  showing a zero, because zero segments and "not loaded yet" are different
 *  facts and this whole system is built on not confusing them.
 */
export default function CommandBar({ counts, online, onPick }) {
  return (
    <header className="bar">
      <div className="mark">
        <b>CITY<i>//</i>DISRUPTION</b>
        <span>Udupi · Manipal digital twin</span>
      </div>

      <div className="bar-search">
        <SearchBox onPick={onPick} />
      </div>

      <div className="bar-readout">
        {counts && (
          <>
            <div>
              <b className="display">{counts.roads.toLocaleString()}</b>
              <span className="cap">Road segments</span>
            </div>
            <div>
              <b className="display">{counts.facilities}</b>
              <span className="cap">Mapped assets</span>
            </div>
          </>
        )}
        <div>
          <span className={`status${online ? '' : ' status-off'}`}>
            <i />
            <span>{online ? 'Network loaded' : 'Loading'}</span>
          </span>
        </div>
      </div>
    </header>
  )
}

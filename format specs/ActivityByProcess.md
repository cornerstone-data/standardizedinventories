## Activity-By-Process Format
Provides the activity an emission process was calculated from - fuel burned, material
processed - as the source reported it. Currently only applies to NEI.

NEI repeats the calculation parameter on every pollutant row of the process it belongs
to, with the same value on each, so one row per process is lossless. Amounts and units
are the source's own and are **not** converted to reference units; a row is only
comparable with another row of the same `ActivityUnit` and `ActivityMaterial`.

Field | Type | Required? | Description
----- | ---- | --------  | -----------
FacilityID | String | Y | a unique identification number used by the inventory to track the facility
UnitID | String | Y | ID of the emissions unit within the facility
ProcessID | String | Y | ID of the emissions process within the unit
Process | String | N | [NEI: EPA Source Classification Codes](https://ofmpub.epa.gov/sccsearch/)
ActivityType | String | N | Whether the parameter is an input (`I`), an output (`O`) or another basis (`E`) for the process
ActivityAmount | Numeric | Y | The amount of the parameter in the source's own unit
ActivityUnit | String | Y | Unit the source reported the amount in, e.g. `E6FT3`, `TON`, `E3GAL`
ActivityMaterial | String | N | Source's code for the material the parameter describes, e.g. natural gas, bituminous coal

### Availability

The NEI point exports for **2012-2013 and 2015-2023** carry these fields. The 2011 and
2014 exports use a narrower layout that does not, and no activity file is written for
those years.

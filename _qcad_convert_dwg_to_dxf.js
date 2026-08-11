include("scripts/library.js");
include("scripts/Tools/arguments.js");

function main() {
    if (args.length < 3) {
        print("Usage: qcad -no-gui -autostart _qcad_convert_dwg_to_dxf.js <input.dwg> <output.dxf>");
        return;
    }

    var inputFile = args[args.length - 2];
    var outputFile = args[args.length - 1];

    if (!new QFileInfo(inputFile).isAbsolute()) {
        inputFile = RSettings.getLaunchPath() + QDir.separator + inputFile;
    }
    if (!new QFileInfo(outputFile).isAbsolute()) {
        outputFile = RSettings.getLaunchPath() + QDir.separator + outputFile;
    }

    var storage = new RMemoryStorage();
    var spatialIndex = new RSpatialIndexSimple();
    var doc = new RDocument(storage, spatialIndex);
    var di = new RDocumentInterface(doc);

    if (di.importFile(inputFile) != RDocumentInterface.IoErrorNoError) {
        qWarning("Cannot import file:", inputFile);
        return;
    }

    di.exportFile(outputFile, "R24 (2010) DXF");
    print("Converted:");
    print("  from: " + inputFile);
    print("  to  : " + outputFile);
}

if (typeof(including)=='undefined' || including===false) {
    main();
}

-- @statement
PRAGMA foreign_keys=ON;
-- @statement
PRAGMA journal_mode=WAL;
-- @statement
PRAGMA busy_timeout=5000;
-- @statement
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
-- @statement
INSERT OR IGNORE INTO metadata VALUES('schema_version','1');
-- @statement
INSERT OR IGNORE INTO metadata VALUES('world_revision','0');
-- @statement
CREATE TABLE IF NOT EXISTS entities(
 rowid INTEGER PRIMARY KEY,id TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,
 parent_id TEXT,actor_id TEXT,asset_id TEXT,label TEXT,class TEXT,path TEXT,
 x0 REAL,x1 REAL,y0 REAL,y1 REAL,z0 REAL,z1 REAL,
 source TEXT NOT NULL,generation TEXT,revision INTEGER NOT NULL
);
-- @statement
CREATE INDEX IF NOT EXISTS entity_kind ON entities(kind,id);
-- @statement
CREATE INDEX IF NOT EXISTS entity_parent ON entities(parent_id,id);
-- @statement
CREATE INDEX IF NOT EXISTS entity_actor ON entities(actor_id,id);
-- @statement
CREATE INDEX IF NOT EXISTS entity_asset ON entities(asset_id,id);
-- @statement
CREATE INDEX IF NOT EXISTS entity_path ON entities(path);
-- @statement
CREATE INDEX IF NOT EXISTS entity_package ON entities(json_extract(source,'$.package'));
-- @statement
CREATE INDEX IF NOT EXISTS bound_x0 ON entities(x0);
-- @statement
CREATE INDEX IF NOT EXISTS bound_x1 ON entities(x1);
-- @statement
CREATE INDEX IF NOT EXISTS bound_y0 ON entities(y0);
-- @statement
CREATE INDEX IF NOT EXISTS bound_y1 ON entities(y1);
-- @statement
CREATE INDEX IF NOT EXISTS bound_z0 ON entities(z0);
-- @statement
CREATE INDEX IF NOT EXISTS bound_z1 ON entities(z1);
-- @statement
CREATE VIRTUAL TABLE IF NOT EXISTS spatial_bounds USING rtree(rowid,x0,x1,y0,y1,z0,z1);
-- @statement
CREATE TRIGGER IF NOT EXISTS entity_insert AFTER INSERT ON entities WHEN NEW.x0 IS NOT NULL BEGIN
 INSERT INTO spatial_bounds VALUES(NEW.rowid,NEW.x0,NEW.x1,NEW.y0,NEW.y1,NEW.z0,NEW.z1);
END;
-- @statement
DROP TRIGGER IF EXISTS entity_update;
-- @statement
CREATE TRIGGER entity_update AFTER UPDATE OF x0,x1,y0,y1,z0,z1 ON entities BEGIN
 DELETE FROM spatial_bounds WHERE rowid=OLD.rowid;
 INSERT INTO spatial_bounds SELECT NEW.rowid,NEW.x0,NEW.x1,NEW.y0,NEW.y1,NEW.z0,NEW.z1 WHERE NEW.x0 IS NOT NULL;
END;
-- @statement
CREATE TRIGGER IF NOT EXISTS entity_delete AFTER DELETE ON entities BEGIN
 DELETE FROM spatial_bounds WHERE rowid=OLD.rowid;
END;
-- @statement
CREATE TABLE IF NOT EXISTS relationships(source TEXT NOT NULL,target TEXT NOT NULL,kind TEXT NOT NULL,PRIMARY KEY(source,target,kind));
-- @statement
CREATE INDEX IF NOT EXISTS relationship_target ON relationships(target,kind,source);
-- @statement
CREATE TABLE IF NOT EXISTS changes(revision INTEGER NOT NULL,entity_id TEXT NOT NULL,type TEXT NOT NULL,before_json TEXT,after_json TEXT,PRIMARY KEY(revision,entity_id));
-- @statement
CREATE INDEX IF NOT EXISTS change_entity ON changes(entity_id,revision);
-- @statement
CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,state TEXT NOT NULL CHECK(state IN ('BUILDING','READY','INVALID')),timestamp TEXT NOT NULL,map TEXT NOT NULL);
-- @statement
CREATE TABLE IF NOT EXISTS geometry(hash TEXT PRIMARY KEY,path TEXT NOT NULL,metadata TEXT NOT NULL);
-- @statement
CREATE TABLE IF NOT EXISTS class_schemas(class TEXT PRIMARY KEY,source TEXT NOT NULL);
-- @statement
CREATE TABLE IF NOT EXISTS package_sources(path TEXT PRIMARY KEY,signature TEXT NOT NULL);
-- @statement
CREATE INDEX IF NOT EXISTS entity_search ON entities(id,kind,label,path,class);
-- @statement
CREATE VIRTUAL TABLE IF NOT EXISTS entity_text USING fts5(label,path,class,content='entities',content_rowid='rowid',tokenize='trigram',detail=none);
-- @statement
CREATE TRIGGER IF NOT EXISTS entity_text_insert AFTER INSERT ON entities BEGIN
 INSERT INTO entity_text(rowid,label,path,class) VALUES(NEW.rowid,NEW.label,NEW.path,NEW.class);
END;
-- @statement
CREATE TRIGGER IF NOT EXISTS entity_text_delete AFTER DELETE ON entities BEGIN
 INSERT INTO entity_text(entity_text,rowid,label,path,class) VALUES('delete',OLD.rowid,OLD.label,OLD.path,OLD.class);
END;
-- @statement
CREATE TRIGGER IF NOT EXISTS entity_text_update AFTER UPDATE OF label,path,class ON entities
WHEN OLD.label IS NOT NEW.label OR OLD.path IS NOT NEW.path OR OLD.class IS NOT NEW.class BEGIN
 INSERT INTO entity_text(entity_text,rowid,label,path,class) VALUES('delete',OLD.rowid,OLD.label,OLD.path,OLD.class);
 INSERT INTO entity_text(rowid,label,path,class) VALUES(NEW.rowid,NEW.label,NEW.path,NEW.class);
END;
-- @statement
INSERT INTO entity_text(entity_text) SELECT 'rebuild' WHERE NOT EXISTS(SELECT 1 FROM metadata WHERE key='text_search_version' AND value='1');
-- @statement
INSERT OR REPLACE INTO metadata VALUES('text_search_version','1');

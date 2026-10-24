"""Author-fixed bilingual synthetic cases; run before any inference."""
import json
from pathlib import Path

# Pairs share meaning across languages; count each language separately, not as
# independent real-world observations. No product identifier predicts the label.
TRAIN = {
'available': [
('In stock. Buy now for immediate dispatch.','在庫あり。今すぐ購入でき、即日発送します。'),
('Available now. Add this item to your cart.','販売中です。カートに追加できます。'),
('We have stock and accept orders today.','在庫を確保しており、本日の注文を受け付けます。'),
('Ready to ship; purchase this item online.','発送可能です。この商品をオンラインで購入できます。'),
('The item is available for purchase today.','本日、この商品をご購入いただけます。'),
('Stock has arrived. Ordering is enabled.','入荷しました。注文可能です。')],
'sold_out': [
('Sold out. Orders cannot be placed.','売り切れです。注文できません。'),
('Out of stock. Purchasing is unavailable.','在庫切れです。購入できません。'),
('No units remain and orders are closed.','残数はゼロで、注文受付を停止しています。'),
('This item is unavailable until restocked.','再入荷までこの商品は購入不可です。'),
('All stock is gone. We cannot accept orders.','全て完売しました。注文を受け付けられません。'),
('Currently sold out; wait for a restock.','現在は品切れです。再入荷をお待ちください。')],
'preorder': [
('Preorder now. Ships after next month\'s release.','予約受付中です。来月の発売後に発送します。'),
('Reserve this upcoming item for delivery on release.','発売予定の商品を予約すると発売日にお届けします。'),
('Advance orders are open; this is not released yet.','先行予約を受け付けています。まだ未発売です。'),
('Accepting preorders for next season.','次のシーズン分の予約を受付中です。'),
('Book your unit before launch. Shipment will be later.','発売前に予約できます。発送は後日です。'),
('You may preorder the new model, shipping in December.','新モデルは予約注文できます。発送は12月です。')],
'unknown': [
('This page describes the material and dimensions only.','このページには素材と寸法のみを掲載しています。'),
('Contact the store to learn availability.','在庫状況は店舗へお問い合わせください。'),
('Stock status has not been provided.','在庫状況は記載されていません。'),
('A review of the product; no ordering information.','商品のレビューです。注文に関する情報はありません。'),
('Availability cannot be confirmed from this page.','このページからは販売状況を確認できません。'),
('Sign in to view current stock information.','現在の在庫情報を見るにはログインが必要です。')]
}
TEST = {
'available': [
('Three units remain, and you can place an order today.','残り3点あり、本日ご注文いただけます。'),
('Previously sold out. Replenished this morning; checkout is open.','以前は完売していましたが、今朝補充されました。現在は購入手続きができます。'),
('Not a preorder: we can dispatch your purchase tomorrow.','予約商品ではありません。ご購入分は明日出荷できます。'),
('The old blue version is sold out; this red item is in stock.','旧青色モデルは売り切れですが、この赤色商品は在庫があります。'),
('Our warehouse can fulfil this order immediately.','倉庫からこの注文分をすぐに出荷できます。'),
('The shop reopened sales after replenishing its shelves.','商品を補充したため、店は販売を再開しました。')],
'sold_out': [
('Zero remaining. The order button has been disabled.','残り0点です。注文ボタンは無効になっています。'),
('Yesterday we had stock. Today all units are gone and orders have stopped.','昨日は在庫がありました。本日は全てなくなり注文を停止しました。'),
('Reservations are not accepted and no units are available.','予約は受け付けておらず、在庫もありません。'),
('The charger is in stock, but this laptop is sold out.','充電器は在庫がありますが、このノートパソコンは売り切れです。'),
('Every piece has been purchased; there is nothing left to sell.','全品が購入され、販売できるものは残っていません。'),
('The last unit was just purchased. Please wait for replenishment.','最後の1点がたった今購入されました。補充をお待ちください。')],
'preorder': [
('Pay to reserve a unit today; delivery starts when it launches next quarter.','本日お支払いいただくと1点を確保します。配送は来四半期の発売時に開始します。'),
('Not in stock yet, but preorders are open for the November release.','まだ在庫はありませんが、11月発売分の予約注文を受け付けています。'),
('You cannot receive it now. Reserve for delivery after the launch date.','今すぐ受け取ることはできません。発売日後のお届け分を予約できます。'),
('The previous model is in stock; this new model is preorder only.','旧モデルは在庫があります。この新モデルは予約のみです。'),
('Secure an upcoming unit ahead of launch by placing an advance order.','発売に先立ち先行注文すると、発売予定分を確保できます。'),
('Production begins next week. We are taking reservations for future delivery.','製造は来週開始します。将来のお届け分の予約を受け付けています。')],
'unknown': [
('The product weighs 230 grams and comes in four colors.','商品は重さ230グラムで4色展開です。'),
('An old review said in stock. No current sales status is shown.','古いレビューには在庫ありと書かれています。現在の販売状況の表示はありません。'),
('This is an example of the words sold out, not the status of this product.','これは「売り切れ」という表現の例であり、この商品の状態ではありません。'),
('Other products can be ordered. The status of this item is not stated.','他の商品は注文できます。この商品の状態は記載されていません。'),
('The site is temporarily unavailable, so we could not check the item.','サイトが一時的に利用できないため、商品を確認できませんでした。'),
('Availability is hidden until you choose a delivery destination.','配送先を選ぶまでは在庫状況が表示されません。')]
}
rows=[]
for split, groups in [('train',TRAIN),('test',TEST)]:
 for label,pairs in groups.items():
  for i,pair in enumerate(pairs):
   for lang,text in zip(('en','ja'),pair):
    rows.append(dict(id=f'{split}-{label}-{i}-{lang}',pair=f'{split}-{label}-{i}',split=split,lang=lang,text=text,label=label,kind=('direct' if split=='train' or i==0 else 'context' if i<4 else 'paraphrase')))
fixture={'provenance':'Hand-authored synthetic bilingual pairs; labels fixed before model inference. Not sampled from real sites.', 'labels':['available','sold_out','preorder','unknown'],'rows':rows}
Path(__file__).with_name('fixture.json').write_text(json.dumps(fixture,ensure_ascii=False,indent=2)+'\n')

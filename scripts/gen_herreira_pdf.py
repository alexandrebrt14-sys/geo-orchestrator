"""Gera PDF da apresentacao Herreira Joias sobre cultura empresarial."""
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.colors import HexColor, white, black
from reportlab.pdfgen import canvas

W, H = landscape(A4)
GOLD = HexColor('#b8860b')
GOLD_LIGHT = HexColor('#f5e6c8')
DARK = HexColor('#1a1a1a')
GRAY = HexColor('#6b6b6b')
RED = HexColor('#dc2626')
GREEN = HexColor('#2e844a')
BG = HexColor('#faf8f5')
OUT = "C:/Sandyboxclaude/herreira_cultura_empresarial.pdf"

c = canvas.Canvas(OUT, pagesize=landscape(A4))

def bg(color=BG):
    c.setFillColor(color); c.rect(0,0,W,H,fill=1,stroke=0)

def lab(t, y, col=GOLD):
    c.setFillColor(col); c.setFont('Helvetica-Bold',10); c.drawString(60,y,t)

def ttl(t, y, sz=26, col=black):
    c.setFillColor(col); c.setFont('Helvetica-Bold',sz)
    for i,ln in enumerate(t.split('\n')): c.drawString(60, y-i*sz*1.3, ln)

def sub(t, y, col=GRAY):
    c.setFillColor(col); c.setFont('Helvetica',11); c.drawString(60,y,t)

def qt(t, a, y=70):
    c.setFillColor(GOLD); c.setFont('Helvetica-Oblique',11); c.drawCentredString(W/2,y+15,'"'+t+'"')
    c.setFillColor(GRAY); c.setFont('Helvetica',9); c.drawCentredString(W/2,y,'-- '+a)

def bul(items, x, y, col=GRAY):
    c.setFont('Helvetica',11)
    for i,it in enumerate(items):
        c.setFillColor(col); c.drawString(x, y-i*17, it)

def stat(v, l, x, y, col=GOLD):
    c.setFillColor(col); c.setFont('Helvetica-Bold',30); c.drawCentredString(x,y,v)
    c.setFillColor(GRAY); c.setFont('Helvetica',9); c.drawCentredString(x,y-18,l)

def gline(y):
    c.setStrokeColor(GOLD); c.setLineWidth(2); c.line(60,y,200,y)

# S1 CAPA
bg(DARK)
c.setFillColor(GOLD); c.setFont('Helvetica-Bold',12); c.drawCentredString(W/2,H-100,'HERREIRA JOIAS')
c.setFillColor(white); c.setFont('Helvetica-Bold',34); c.drawCentredString(W/2,H-165,'Transformacao Cultural:')
c.drawCentredString(W/2,H-210,'O Pilar Invisivel do Crescimento')
c.setFillColor(GOLD_LIGHT); c.setFont('Helvetica',15); c.drawCentredString(W/2,H-265,'Estrategias para alinhar cultura, pessoas e resultado')
qt('A cultura come a estrategia no cafe da manha.','Peter Drucker',y=105)
c.setFillColor(GRAY); c.setFont('Helvetica',10); c.drawCentredString(W/2,65,'Assessoria Brasil GEO | 2026')
c.showPage()

# S2 POR QUE CULTURA
bg()
lab('POR QUE CULTURA IMPORTA',H-50); gline(H-55)
ttl('Cultura: o ativo que ninguem ve\nmas que define tudo.',H-90)
bul(['Empresas com cultura forte crescem 3x mais rapido','70% dos CEOs priorizam cultura sobre estrategia','+20% de produtividade com valores claros','Reducao de turnover em ate 40%'],60,H-175)
bul(['R$ 12,1M de receita na Herreira (+26,8%)','Mas prejuizo de R$ 273K no grupo','Zero politicas de RH ou avaliacao','Crescimento sem cultura e insustentavel'],450,H-175)
qt('As pessoas certas nos lugares certos fazem as coisas certas.','Jim Collins, Good to Great')
c.showPage()

# S3 DADOS
bg(white)
lab('DADOS QUE COMPROVAM',H-50); gline(H-55)
ttl('O custo de ignorar a cultura\ne mensuravel.',H-90)
stat('30%','mais crescimento',150,H-195,GOLD)
stat('R$1,4M','perdidos/ano',350,H-195,GOLD)
stat('40%','menos turnover',550,H-195,GOLD)
stat('3x','mais receita',750,H-195,GOLD)
qt('O que e medido, e gerenciado.','Peter Drucker')
c.showPage()

# S4 HISTORIA
bg()
lab('A HISTORIA',H-50); gline(H-55)
ttl('De montagem de semijoias\na R$ 12 milhoes em receita.',H-90)
bul(['2008: Patricia e Alexandre fundam a Herreira','2011: Investem R$ 1M em fabrica propria','110 funcionarios, vendas para EUA e Europa','10-15 mil pecas/mes no atacado','Presenca em novelas da Globo'],60,H-175)
bul(['56 anos de tradicao joalheira','Celebridades vestem Herreira','Crescimento de 26,8% em 2025','Hora de profissionalizar'],450,H-175)
qt('Resultados vem da exploracao de oportunidades, nao de problemas.','Peter Drucker')
c.showPage()

# S5 RH
bg(white)
lab('DIAGNOSTICO: RECURSOS HUMANOS',H-50,RED); gline(H-55)
ttl('Zero politicas. Zero avaliacoes.\nZero plano de carreira.',H-90)
bul(['0 programas de capacitacao','0 mecanismos de retencao','0 pesquisa de clima','0 avaliacao de desempenho','0 plano de carreira'],60,H-175,RED)
bul(['Alto turnover = custo invisivel','Aulore: pessoal consome 38,5%','Sem comunicacao interna','Sem programa Prata da Casa'],450,H-175)
qt('Nao ha nada tao inutil quanto fazer eficientemente o que nao deveria ser feito.','Peter Drucker')
c.showPage()

# S6 PRO LABORE
bg()
lab('O PROBLEMA CENTRAL',H-50,RED); gline(H-55)
ttl('Socias retiram mais do que\na empresa consegue gerar.',H-90)
stat('-60,9%','Herreira (controlavel)',170,H-200,GREEN)
stat('-363%','Aulore (CRITICO)',420,H-200,RED)
stat('-230%','Vitesse (CRITICO)',670,H-200,RED)
sub('Pro labore total: R$ 1,6M/ano. Lucro operacional: R$ 1,5M. Resultado: -R$ 273K.',H-290)
qt('Lucro nao e um objetivo. E condicao para sobrevivencia.','Peter Drucker')
c.showPage()

# S7 CUSTO
bg(DARK)
lab('O CUSTO DA INACAO',H-50,GOLD)
c.setFillColor(white); c.setFont('Helvetica-Bold',28)
c.drawString(60,H-105,'R$ 1,4 milhao por ano.')
c.drawString(60,H-145,'Esse e o preco de nao mudar.')
stat('R$760K','Custos acima do benchmark',170,H-240,GOLD)
stat('R$366K','Ineficiencia em pessoal',420,H-240,GOLD)
stat('R$360K','Despesas renegociaveis',670,H-240,GOLD)
qt('Se voce quer algo novo, precisa parar de fazer algo velho.','Peter Drucker',y=90)
c.showPage()

# S8 6 PILARES
bg()
lab('O QUE MUDAR',H-50); gline(H-55)
ttl('6 pilares para transformar\na cultura da Herreira.',H-90)
for i,(n,d) in enumerate([('01 Valores','Definir valores que guiam decisoes'),('02 Rituais','Reunioes mensais, celebracoes, feedback'),('03 Metricas','KPIs por funcao, avaliacao trimestral'),('04 Lideranca','Gestoras como exemplos da cultura'),('05 Desenvolvimento','Capacitacao, carreira, mentoria'),('06 Reconhecimento','Programa Prata da Casa, bonus')]):
    r,co=i//3,i%3; x=60+co*270; y=H-195-r*75
    c.setFillColor(GOLD); c.setFont('Helvetica-Bold',13); c.drawString(x,y,n)
    c.setFillColor(GRAY); c.setFont('Helvetica',10); c.drawString(x,y-16,d)
c.showPage()

# S9 BENCHMARK
bg(white)
lab('BENCHMARK VIVARA',H-50); gline(H-55)
ttl('Onde a cultura faz a diferenca.',H-90)
for i,(m,h,v,g) in enumerate([('Metrica','Herreira','Vivara','Gap'),('Crescimento','+26,8%','+16,2%','Herreira ganha'),('Margem bruta','52,6%','70-74%','-18pp'),('EBITDA','12,7%','25,5%','-13pp'),('Margem liquida','-1,3%','25,4%','CRITICO'),('E-commerce','Incipiente','Robusto','Gap critico')]):
    y=H-175-i*22; b=i==0
    c.setFont('Helvetica-Bold' if b else 'Helvetica',11)
    c.setFillColor(black); c.drawString(60,y,m)
    c.setFillColor(GOLD if not b else black); c.drawString(250,y,h)
    c.setFillColor(GRAY if not b else black); c.drawString(400,y,v)
    c.setFillColor(RED if 'CRITICO' in g or 'Gap' in g else GREEN if not b else black); c.drawString(550,y,g)
qt('Voce nao pode gerenciar o que nao pode medir.','Peter Drucker')
c.showPage()

# S10 PROJECAO
bg()
lab('PROJECAO FINANCEIRA',H-50); gline(H-55)
ttl('De prejuizo a lucro em 12 meses.',H-90)
stat('-R$273K','Resultado 2025',170,H-200,RED)
stat('+R$1,4M','Melhorias/ano',420,H-200,GOLD)
stat('+R$1,1M','Projecao 2026',670,H-200,GREEN)
sub('Investimento: R$ 0 (gestao) + R$ 3-8K (governanca) + R$ 3-8K/mes (marketing)',H-300)
qt('A melhor maneira de prever o futuro e cria-lo.','Peter Drucker')
c.showPage()

# S11 FINAL
bg(DARK)
c.setFillColor(white); c.setFont('Helvetica-Bold',30)
for i,ln in enumerate(['Cultura nao e o que voce','escreve na parede.','','E o que acontece quando','ninguem esta olhando.']):
    c.drawCentredString(W/2, H-175-i*42, ln)
c.setFillColor(GOLD); c.setFont('Helvetica-Oblique',14); c.drawCentredString(W/2,155,'-- Adaptado de Peter Drucker')
c.setFillColor(GOLD_LIGHT); c.setFont('Helvetica-Bold',12); c.drawCentredString(W/2,110,'HERREIRA JOIAS  |  O proximo capitulo comeca agora.')
c.setFillColor(GRAY); c.setFont('Helvetica',9); c.drawCentredString(W/2,80,'Assessoria por Alexandre Caramaschi | CEO da Brasil GEO | alexandrecaramaschi.com')

c.save()
import os
sz = os.path.getsize(OUT)
print(f"PDF salvo: {OUT}")
print(f"Tamanho: {sz:,} bytes ({sz//1024} KB)")
print(f"Paginas: 11 slides")

module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire update_en,
    input wire state_q,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (update_en && $past(update_en)) |-> $stable(state_q));

endmodule


bind keymgr_ctrl sva_checker sva_checker_inst (.*);
